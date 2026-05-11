import os, struct, tempfile, subprocess, requests, telebot, threading, time
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, abort

BOT_TOKEN   = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
NETLIFY_URL = "https://loquacious-speculoos-2613c5.netlify.app/.netlify/functions/get_bypass_config"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

# State tracking
waiting_for_video  = {}   # chat_id -> "binary" or "time"
waiting_for_fps    = {}   # chat_id -> {"file_id": ..., "file_name": ..., "file_size": ...}

# Keep Render awake — pings self every 10 min
def keep_alive():
    while True:
        time.sleep(600)
        try: requests.get(WEBHOOK_URL, timeout=10)
        except: pass

threading.Thread(target=keep_alive, daemon=True).start()

# ──────────────────────────────────────────
#  PATCH LOGIC
# ──────────────────────────────────────────
def get_payload():
    r = requests.post(NETLIFY_URL, timeout=15)
    r.raise_for_status()
    d = r.json()
    return int(d["payload"]) if d.get("success") else None

def binary_patch(raw, payload):
    """Same as extension: find elst, write payload at +8. Falls back to mvhd."""
    data = bytearray(raw)
    idx = raw.find(b"elst")
    if idx != -1:
        struct.pack_into(">I", data, idx + 8, payload)
        return bytes(data), "elst"
    idx = raw.find(b"mvhd")
    if idx != -1:
        version = raw[idx + 4]
        if version == 0:
            old_ts  = struct.unpack_from(">I", raw, idx + 12)[0]
            old_dur = struct.unpack_from(">I", raw, idx + 16)[0]
            new_ts  = 60000
            new_dur = int(old_dur * new_ts / old_ts) if old_ts > 0 else old_dur
            struct.pack_into(">I", data, idx + 12, new_ts)
            struct.pack_into(">I", data, idx + 16, new_dur)
        else:
            old_ts  = struct.unpack_from(">I", raw, idx + 20)[0]
            old_dur = struct.unpack_from(">Q", raw, idx + 24)[0]
            new_ts  = 60000
            new_dur = int(old_dur * new_ts / old_ts) if old_ts > 0 else old_dur
            struct.pack_into(">I", data, idx + 20, new_ts)
            struct.pack_into(">Q", data, idx + 24, new_dur)
        return bytes(data), "mvhd"
    return None, None

def time_patch(input_path, output_path, original_fps, target_fps):
    """Same as extension: ffmpeg -itsscale (orig/target) -i in -c copy out"""
    scale = original_fps / target_fps
    result = subprocess.run(
        ["ffmpeg", "-y", "-itsscale", str(scale), "-i", input_path,
         "-c", "copy", output_path],
        capture_output=True, timeout=120
    )
    return result.returncode == 0

def detect_fps(input_path):
    """Detect FPS using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        num, den = result.stdout.strip().split("/")
        return round(int(num) / int(den), 2)
    except:
        return None

def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except:
        return False

# ──────────────────────────────────────────
#  /start
# ──────────────────────────────────────────
@bot.message_handler(commands=["start"])
def start(msg):
    cid = msg.chat.id
    waiting_for_video.pop(cid, None)
    waiting_for_fps.pop(cid, None)
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(
        InlineKeyboardButton("⚡ Binary Patch", callback_data="binary_patch"),
        InlineKeyboardButton("🕒 Time Patch",   callback_data="time_patch"),
    )
    bot.send_message(
        cid,
        "╔══════════════════╗\n"
        "║   JV-60FPS BOT   ║\n"
        "╚══════════════════╝\n\n"
        "Choose a patch mode:",
        reply_markup=mk
    )

# ──────────────────────────────────────────
#  Buttons
# ──────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def handle_cb(call):
    cid = call.message.chat.id
    bot.answer_callback_query(call.id)

    if call.data == "binary_patch":
        waiting_for_video[cid] = "binary"
        waiting_for_fps.pop(cid, None)
        bot.send_message(cid,
            "⚡ Binary Patch Mode\n\n"
            "Send your video as a File (not as video).\n"
            "Bot injects the 120FPS bypass payload and returns the patched file."
        )

    elif call.data == "time_patch":
        waiting_for_video[cid] = "time"
        waiting_for_fps.pop(cid, None)
        bot.send_message(cid,
            "🕒 Time Patch Mode\n\n"
            "Send your video as a File.\n"
            "Bot will detect FPS and ask which target FPS you want."
        )

    elif call.data in ("fps_60", "fps_30"):
        target = 60 if call.data == "fps_60" else 30
        info = waiting_for_fps.pop(cid, None)
        if not info:
            bot.send_message(cid, "Session expired. Send /start and try again.")
            return
        threading.Thread(target=do_time_patch, args=(cid, info, target)).start()

# ──────────────────────────────────────────
#  Video / Document handler
# ──────────────────────────────────────────
@bot.message_handler(content_types=["video", "document"])
def handle_video(msg):
    cid = msg.chat.id
    mode = waiting_for_video.pop(cid, None)

    if not mode:
        bot.send_message(cid, "Tap ⚡ Binary Patch or 🕒 Time Patch first, then send your video.")
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
            waiting_for_video[cid] = mode  # restore state
            return
        file_id   = doc.file_id
        file_name = name if name else "video.mp4"
        file_size = doc.file_size or 0

    if file_size > 20 * 1024 * 1024:
        bot.send_message(cid,
            "⚠️ File is over 20MB — Telegram limit.\n"
            "Compress it below 20MB and try again."
        )
        return

    info = {"file_id": file_id, "file_name": file_name, "file_size": file_size}

    if mode == "binary":
        threading.Thread(target=do_binary_patch, args=(cid, info)).start()
    else:
        threading.Thread(target=ask_fps, args=(cid, info)).start()

# ──────────────────────────────────────────
#  Binary patch worker
# ──────────────────────────────────────────
def do_binary_patch(cid, info):
    status = bot.send_message(cid, "⏳ Downloading video...")
    try:
        raw = download_file(info["file_id"])

        bot.edit_message_text("🔍 Fetching bypass payload...", cid, status.message_id)
        payload = get_payload()
        if payload is None:
            bot.edit_message_text("❌ Cloud payload fetch failed. Try again.", cid, status.message_id)
            return

        bot.edit_message_text("💉 Injecting binary payload...", cid, status.message_id)
        patched, method = binary_patch(raw, payload)
        if patched is None:
            bot.edit_message_text("❌ Could not patch this file.\nMake sure it is a valid MP4.", cid, status.message_id)
            return

        bot.edit_message_text("📤 Sending patched video...", cid, status.message_id)
        out_name = info["file_name"].lower().replace(".mp4", "_jv_120fps.mp4")

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(patched)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            bot.send_document(cid, f,
                caption="✅ Patch complete! [" + method + "]\n\nUpload to TikTok now 🚀",
                visible_file_name=out_name
            )
        os.remove(tmp_path)
        bot.delete_message(cid, status.message_id)

    except Exception as e:
        safe_edit(cid, status.message_id, "❌ Error: " + str(e))

# ──────────────────────────────────────────
#  Time patch — ask FPS
# ──────────────────────────────────────────
def ask_fps(cid, info):
    status = bot.send_message(cid, "⏳ Downloading video...")
    try:
        raw = download_file(info["file_id"])

        # Save to temp file to detect FPS
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        info["tmp_path"] = tmp_path
        info["raw"] = raw  # keep in memory for fast re-use

        bot.edit_message_text("🔍 Detecting FPS...", cid, status.message_id)

        fps = detect_fps(tmp_path) if check_ffmpeg() else None
        fps_text = f"{fps} FPS detected" if fps else "FPS unknown"

        waiting_for_fps[cid] = info
        bot.delete_message(cid, status.message_id)

        mk = InlineKeyboardMarkup(row_width=2)
        mk.add(
            InlineKeyboardButton("60 FPS", callback_data="fps_60"),
            InlineKeyboardButton("30 FPS", callback_data="fps_30"),
        )
        bot.send_message(
            cid,
            f"📊 {fps_text}\n\nChoose target FPS:",
            reply_markup=mk
        )

    except Exception as e:
        safe_edit(cid, status.message_id, "❌ Error: " + str(e))

# ──────────────────────────────────────────
#  Time patch worker
# ──────────────────────────────────────────
def do_time_patch(cid, info, target_fps):
    status = bot.send_message(cid, f"⏳ Patching to {target_fps} FPS...")
    try:
        tmp_path = info.get("tmp_path")
        if not tmp_path or not os.path.exists(tmp_path):
            # Re-download if temp file gone
            raw = download_file(info["file_id"])
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name

        if not check_ffmpeg():
            bot.edit_message_text("❌ FFmpeg not available on this server.", cid, status.message_id)
            return

        # Detect original FPS
        original_fps = detect_fps(tmp_path) or 30.0

        out_path = tmp_path.replace(".mp4", f"_out_{target_fps}fps.mp4")

        bot.edit_message_text(f"💉 Converting {original_fps} → {target_fps} FPS...", cid, status.message_id)

        ok = time_patch(tmp_path, out_path, original_fps, target_fps)
        if not ok:
            bot.edit_message_text("❌ FFmpeg failed. Make sure it's a valid MP4.", cid, status.message_id)
            return

        # Step 2 — inject binary payload into the time-patched file
        bot.edit_message_text("💉 Injecting binary payload...", cid, status.message_id)
        payload = get_payload()
        if payload is None:
            bot.edit_message_text("❌ Cloud payload fetch failed. Try again.", cid, status.message_id)
            return

        with open(out_path, "rb") as f:
            patched_raw = f.read()

        final_raw, method = binary_patch(patched_raw, payload)
        if final_raw is None:
            bot.edit_message_text("❌ Binary inject failed on patched file.", cid, status.message_id)
            return

        # Write final result back
        with open(out_path, "wb") as f:
            f.write(final_raw)

        bot.edit_message_text("📤 Sending patched video...", cid, status.message_id)
        out_name = info["file_name"].lower().replace(".mp4", f"_jv_{target_fps}fps.mp4")

        with open(out_path, "rb") as f:
            bot.send_document(cid, f,
                caption=f"✅ Patch complete! Time [{original_fps}→{target_fps} FPS] + Binary [{method}]\n\nUpload to TikTok now 🚀",
                visible_file_name=out_name
            )

        os.remove(tmp_path)
        os.remove(out_path)
        bot.delete_message(cid, status.message_id)

    except Exception as e:
        safe_edit(cid, status.message_id, "❌ Error: " + str(e))

# ──────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────
def download_file(file_id):
    info = bot.get_file(file_id)
    r = requests.get(
        "https://api.telegram.org/file/bot" + BOT_TOKEN + "/" + info.file_path,
        timeout=120
    )
    r.raise_for_status()
    return r.content

def safe_edit(cid, msg_id, text):
    try:
        bot.edit_message_text(text, cid, msg_id)
    except Exception:
        try:
            bot.send_message(cid, text)
        except Exception:
            pass

# ──────────────────────────────────────────
#  Flask webhook
# ──────────────────────────────────────────
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
