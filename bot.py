import os, struct, tempfile, requests, telebot, threading, time
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, abort

BOT_TOKEN   = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
NETLIFY_URL = "https://loquacious-speculoos-2613c5.netlify.app/.netlify/functions/get_bypass_config"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)
waiting_for_video = set()

# keep Render awake — pings self every 10 min
def keep_alive():
    while True:
        time.sleep(600)
        try: requests.get(WEBHOOK_URL, timeout=10)
        except: pass

threading.Thread(target=keep_alive, daemon=True).start()

# ---------- patch ----------
def get_payload():
    r = requests.post(NETLIFY_URL, timeout=15)
    r.raise_for_status()
    d = r.json()
    return int(d["payload"]) if d.get("success") else None

def patch_mp4(raw, payload):
    data = bytearray(raw)

    # Try elst first (same as extension)
    idx = raw.find(b"elst")
    if idx != -1:
        struct.pack_into(">I", data, idx + 8, payload)
        return bytes(data), "elst"

    # Fallback: patch mvhd timescale — works on ALL mp4 files
    idx = raw.find(b"mvhd")
    if idx != -1:
        version = raw[idx + 4]
        if version == 0:
            # version 0: timescale at offset +12, duration at +16
            old_ts  = struct.unpack_from(">I", raw, idx + 12)[0]
            old_dur = struct.unpack_from(">I", raw, idx + 16)[0]
            new_ts  = 60000
            new_dur = int(old_dur * new_ts / old_ts) if old_ts > 0 else old_dur
            struct.pack_into(">I", data, idx + 12, new_ts)
            struct.pack_into(">I", data, idx + 16, new_dur)
        else:
            # version 1: timescale at offset +20, duration at +24 (8 bytes)
            old_ts  = struct.unpack_from(">I", raw, idx + 20)[0]
            old_dur = struct.unpack_from(">Q", raw, idx + 24)[0]
            new_ts  = 60000
            new_dur = int(old_dur * new_ts / old_ts) if old_ts > 0 else old_dur
            struct.pack_into(">I", data, idx + 20, new_ts)
            struct.pack_into(">Q", data, idx + 24, new_dur)
        return bytes(data), "mvhd"

    return None, None

# ---------- /start ----------
@bot.message_handler(commands=["start"])
def start(msg):
    waiting_for_video.discard(msg.chat.id)
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(
        InlineKeyboardButton("⚡ Binary Patch", callback_data="binary_patch"),
        InlineKeyboardButton("🕒 Time Patch",   callback_data="time_patch"),
    )
    bot.send_message(
        msg.chat.id,
        "╔══════════════════╗\n"
        "║   JV-60FPS BOT   ║\n"
        "╚══════════════════╝\n\n"
        "Choose a patch mode:",
        reply_markup=mk
    )

# ---------- buttons ----------
@bot.callback_query_handler(func=lambda c: True)
def handle_cb(call):
    cid = call.message.chat.id
    bot.answer_callback_query(call.id)
    if call.data == "binary_patch":
        waiting_for_video.add(cid)
        bot.send_message(cid,
            "📹 Binary Patch Mode\n\n"
            "Send your video now.\n"
            "Bot injects the 120FPS bypass payload and returns the patched file."
        )
    elif call.data == "time_patch":
        bot.send_message(cid, "🕒 Time Patch — Coming soon!")

# ---------- video ----------
@bot.message_handler(content_types=["video", "document"])
def handle_video(msg):
    cid = msg.chat.id

    if cid not in waiting_for_video:
        bot.send_message(cid, "Tap ⚡ Binary Patch first, then send your video.")
        return

    if msg.content_type == "video":
        file_id   = msg.video.file_id
        file_name = "video_" + msg.video.file_unique_id + ".mp4"
        file_size = msg.video.file_size or 0
    else:
        doc  = msg.document
        mime = doc.mime_type or ""
        name = doc.file_name or ""
        if "video/mp4" not in mime and not name.lower().endswith(".mp4"):
            bot.send_message(cid, "⚠️ Please send an MP4 file.")
            return
        file_id   = doc.file_id
        file_name = name if name else "video.mp4"
        file_size = doc.file_size or 0

    waiting_for_video.discard(cid)
    status = bot.send_message(cid, "⏳ Downloading video...")

    try:
        if file_size > 20 * 1024 * 1024:
            bot.edit_message_text(
                "⚠️ File is over 20MB — Telegram limit.\n"
                "Compress it below 20MB and try again.",
                cid, status.message_id
            )
            return

        info = bot.get_file(file_id)
        raw  = requests.get(
            "https://api.telegram.org/file/bot" + BOT_TOKEN + "/" + info.file_path,
            timeout=120
        ).content

        bot.edit_message_text("🔍 Fetching bypass payload...", cid, status.message_id)

        payload = get_payload()
        if payload is None:
            bot.edit_message_text("❌ Cloud payload fetch failed. Try again.", cid, status.message_id)
            return

        bot.edit_message_text("💉 Injecting binary payload...", cid, status.message_id)

        patched, method = patch_mp4(raw, payload)
        if patched is None:
            bot.edit_message_text(
                "❌ Could not patch this file.\nMake sure it is a valid MP4 file.",
                cid, status.message_id
            )
            return

        bot.edit_message_text("📤 Sending patched video...", cid, status.message_id)

        out_name = file_name.lower().replace(".mp4", "_jv_120fps.mp4")

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(patched)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            bot.send_document(
                cid, f,
                caption="✅ Patch complete! [" + method + "]\n\nUpload to TikTok now 🚀",
                visible_file_name=out_name
            )

        os.remove(tmp_path)
        bot.delete_message(cid, status.message_id)

    except Exception as e:
        try:
            bot.edit_message_text("❌ Error: " + str(e), cid, status.message_id)
        except Exception:
            bot.send_message(cid, "❌ Error: " + str(e))

# ---------- flask ----------
@app.route("/" + BOT_TOKEN, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        bot.process_new_updates(
            [telebot.types.Update.de_json(request.get_data().decode("utf-8"))]
        )
        return "OK", 200
    abort(403)

@app.route("/")
def index():
    return "JV-60FPS Bot is running.", 200

@app.route("/set_webhook")
def set_webhook():
    bot.remove_webhook()
    ok = bot.set_webhook(url=WEBHOOK_URL + "/" + BOT_TOKEN)
    return "Webhook set: " + str(ok), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
