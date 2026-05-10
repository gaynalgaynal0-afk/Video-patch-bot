"""
JV-60FPS Telegram Bot — Binary Patch
Deployed on Render via webhook
"""

import os
import struct
import tempfile
import requests
import json
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, abort

# ─────────────────────────────────────────
#  CONFIG (set as Render env vars)
# ─────────────────────────────────────────
BOT_TOKEN   = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]   # e.g. https://jv60fps-bot.onrender.com
NETLIFY_URL = "https://loquacious-speculoos-2613c5.netlify.app/.netlify/functions/get_bypass_config"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

waiting_for_video = set()

# ─────────────────────────────────────────
#  PATCH LOGIC
# ─────────────────────────────────────────
def get_bypass_payload():
    # FIX 1: added raise_for_status so a Netlify error doesn't silently return None
    r = requests.post(NETLIFY_URL, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("success"):
        return int(data["payload"])   # FIX 2: force int — JSON sometimes returns float
    return None

def patch_video(raw: bytes, payload: int):
    idx = raw.find(b'elst')
    if idx == -1:
        return None
    data = bytearray(raw)
    struct.pack_into('>I', data, idx + 8, payload)
    return bytes(data)

# ─────────────────────────────────────────
#  BOT HANDLERS
# ─────────────────────────────────────────
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

@bot.callback_query_handler(func=lambda c: True)
def handle_cb(call):
    cid = call.message.chat.id
    bot.answer_callback_query(call.id)
    if call.data == "binary_patch":
        waiting_for_video.add(cid)
        # FIX 3: removed MarkdownV2 from this message — the plain text has no
        # special chars that need escaping, but MarkdownV2 was crashing on the
        # period in "patched file." causing MessageTextIsEmpty / bad request
        bot.send_message(
            cid,
            "📹 Binary Patch Mode\n\n"
            "Send your video now.\n"
            "Bot will inject the 120FPS bypass payload and return the patched file."
        )
    elif call.data == "time_patch":
        bot.send_message(cid, "🕒 Time Patch — Coming soon!")

@bot.message_handler(content_types=['video', 'document'])
def handle_video(msg):
    cid = msg.chat.id
    if cid not in waiting_for_video:
        bot.send_message(cid, "Tap ⚡ Binary Patch first, then send your video.")
        return

    if msg.content_type == 'video':
        file_id   = msg.video.file_id
        file_name = f"video_{msg.video.file_unique_id}.mp4"
    else:
        doc = msg.document
        mime = doc.mime_type or ""
        name = doc.file_name or ""
        if "video/mp4" not in mime and not name.lower().endswith('.mp4'):
            bot.send_message(cid, "⚠️ Please send an MP4 file.")
            return
        file_id   = doc.file_id
        file_name = name or "video.mp4"

    waiting_for_video.discard(cid)
    status = bot.send_message(cid, "⏳ Downloading video...")

    try:
        # 1. Download
        info = bot.get_file(file_id)
        # FIX 4: Telegram only allows bot.get_file for files up to 20MB.
        # Files over 20MB need a local bot server or workaround.
        # We check size and warn the user instead of silently crashing.
        file_size = None
        if msg.content_type == 'video' and msg.video.file_size:
            file_size = msg.video.file_size
        elif msg.content_type == 'document' and msg.document.file_size:
            file_size = msg.document.file_size

        if file_size and file_size > 20 * 1024 * 1024:
            bot.edit_message_text(
                "⚠️ File is over 20MB — Telegram bot API limit.\n"
                "Compress your video below 20MB and try again.",
                cid, status.message_id
            )
            return

        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}"
        raw = requests.get(url, timeout=120).content

        bot.edit_message_text("🔍 Fetching bypass payload...", cid, status.message_id)

        # 2. Get cloud payload
        payload = get_bypass_payload()
        if payload is None:
            bot.edit_message_text("❌ Cloud payload fetch failed. Try again.", cid, status.message_id)
            return

        bot.edit_message_text("💉 Injecting binary payload...", cid, status.message_id)

        # 3. Patch
        patched = patch_video(raw, payload)
        if patched is None:
            bot.edit_message_text(
                "❌ 'elst' atom not found in this video.\n"
                "Make sure it's a valid MP4 file.",
                cid, status.message_id
            )
            return

        bot.edit_message_text("📤 Sending patched video...", cid, status.message_id)

        # 4. Send back as document (keeps original quality, no Telegram re-encode)
        out_name = file_name.lower().replace('.mp4', '_jv_120fps.mp4')
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
            tmp.write(patched)
            tmp_path = tmp.name

        with open(tmp_path, 'rb') as f:
            bot.send_document(
                cid, f,
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

# ─────────────────────────────────────────
#  FLASK WEBHOOK
# ─────────────────────────────────────────
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
        return "OK", 200
    abort(403)

@app.route("/", methods=["GET"])
def index():
    return "JV-60FPS Bot is running.", 200

@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    bot.remove_webhook()
    result = bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    return f"Webhook set: {result}", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
