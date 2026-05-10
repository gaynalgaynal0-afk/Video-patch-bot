# JV-60FPS Telegram Bot

Telegram bot that injects 120FPS bypass payload into MP4 files for TikTok uploads.

## Deploy on Render

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New Web Service → connect your repo
3. Set these environment variables:
   - `BOT_TOKEN` — your token from @BotFather
   - `WEBHOOK_URL` — your Render URL, e.g. `https://jv60fps-bot.onrender.com`
4. Deploy → once live, visit `https://your-app.onrender.com/set_webhook` once to register the webhook

## Local / Termux run

```bash
pip install -r requirements.txt
export BOT_TOKEN=your_token
export WEBHOOK_URL=https://your-render-url.onrender.com
python bot.py
```

## How it works

- User taps **⚡ Binary Patch** → sends MP4
- Bot fetches payload from Netlify cloud function
- Finds `elst` atom, writes payload at `elst+8` (same as extension)
- Returns `_jv_120fps.mp4` ready to upload to TikTok
