"""
JV-60FPS Telegram Bot — Binary Patch
=====================================
GitHub: upload ONLY this file + requirements.txt
Render env vars needed: BOT_TOKEN, WEBHOOK_URL
"""

import os, struct, tempfile, requests, telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, abort

BOT_TOKEN   = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
NETLIFY_URL = "https://loquacious-speculoos-2613c5.netlify.app/.netlify/functions/get_bypass_config"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)
waiting_for_video = set()

# ── Patch logic ──────────────────────────
def get_payload():
    r = requests.post(NETLIFY_URL, timeout=15)
    r.raise_for_status()
    d = r.json()
    return int(d["payload"]) if d.get("success") else None

def patch_mp4(raw: bytes, payload: int):
    idx = raw.find(b'elst')
    if idx == -1:
        return None
    data = bytearray(raw)
    struct.pack_into('>I', data, idx + 8, payload)
    return bytes(data)

# ── /start ───────────────────────────────
@bot.message_handler(commands=['start'])
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

# ── Buttons ──────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def handle_cb(call):
    cid = call.message.chat.id
    bot.answer_callback_query(call.id)
    if call.data == "binary_patch":
        waiting_for_video.add(cid)
        bot.send_message(cid,
            "📹 Binary Patch Mode\n\n"
            "Send your video now.\n"
            "Bot will inject the 120FPS bypass payload and return the patched file."
        )
    elif call.data == "time_patch":
        bot.send_message(cid, "🕒 Time Patch — Coming soon!")

# ── Video handler ─────────────────────────
@bot.message_handler(content_types=['video', 'document'])
def handle_video(msg):
    cid = msg.chat.id
    if cid not in waiting_for_video:
        bot.send_message(cid, "Tap ⚡ Binary Patch first, then send your video.")
        return

    if msg.content_type == 'video':
        file_id   = msg.video.file_id
        file_name = f"video_{msg.video.file_unique_id}.mp4"
        file_size = msg.video.file_size
    else:
        doc = msg.document
        mime = doc.mime_type or ""
        name = doc.file_name or ""
        if "video/mp4" not in mime and not name.lower().endswith('.mp4'):
            bot.send_message(cid, "⚠️ Please send an MP4 file.")
            return
        file_id   = doc.file_id
        file_name = name or "video.mp4"
        file_size = doc.file_size

    waiting_for_video.discard(cid)
    status = bot.send_message(cid, "⏳ Downloading video...")

    try:
        if file_size and file_size > 20 * 1024 * 1024:
            bot.edit_message_text(
                "⚠️ File over 20MB — Telegram limit.\nCompress below 20MB and try again.",
                cid, status.message_id
            )
            return

        info = bot.get_file(file_id)
        raw  = requests.get(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}",
            timeout=120
        ).content

        bot.edit_message_text("🔍 Fetching bypass payload...", cid, status.message_id)
        payload = get_payload()
        if payload is None:
            bot.edit_message_text("❌ Cloud payload fetch failed. Try again.", cid, status.message_id)
            return

        bot.edit_message_text("💉 Injecting binary payload...", cid, status.message_id)
        patched = patch_mp4(raw, payload)
        if patched is None:
            bot.edit_message_text(
                "❌ 'elst' atom not found.\nMake sure it's a valid MP4 file.",
                cid, status.message_id
            )
            return

        bot.edit_message_text("📤 Sending patched video...", cid, status.message_id)
        out_name = file_name.lower().replace('.mp4', '_jv_120fps.mp4')

        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
            tmp.write(patched)
            tmp_path = tmp.name

        with open(tmp_path, 'rb') as f:
            bot.send_document(cid, f,
                caption="✅ Patch complete!\n\nUpload to TikTok now 🚀",
                visible_file_name=out_name
            )
        os.remove(tmp_path)
        bot.delete_message(cid, status.message_id)

    except Exception as e:
        try:
            bot.edit_message_text(f"❌ Error: {e}", cid, status.message_id)
        except:
            bot.send_message(cid, f"❌ Error: {e}")

# ── Webhook ───────────────────────────────
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        bot.process_new_updates([telebot.types.Update.de_json(request.get_data().decode("utf-8"))])
        return "OK", 200
    abort(403)

@app.route("/")
def index():
    return "JV-60FPS Bot is running.", 200

@app.route("/set_webhook")
def set_webhook():
    bot.remove_webhook()
    ok = bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    return f"Webhook set: {ok}", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
