"""MusicCheckBot — Telegram bot entry point with webhook support."""

import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, PORT, WEBHOOK_URL
from handlers import (
    cmd_about,
    cmd_analyze,
    cmd_debug,
    cmd_feedback,
    cmd_help,
    cmd_start,
    handle_audio,
    handle_text,
    handle_unsupported,
)

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Validate config ──────────────────────────────────────
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not set! Check your .env file or Railway variables.")
    sys.exit(1)

if not WEBHOOK_URL:
    logger.error("WEBHOOK_URL not set! Needed for webhook mode.")
    sys.exit(1)

# ── Build Telegram Application ───────────────────────────
app_telegram = Application.builder().token(BOT_TOKEN).build()

# Register command handlers
app_telegram.add_handler(CommandHandler("start", cmd_start))
app_telegram.add_handler(CommandHandler("help", cmd_help))
app_telegram.add_handler(CommandHandler("about", cmd_about))
app_telegram.add_handler(CommandHandler("feedback", cmd_feedback))
app_telegram.add_handler(CommandHandler("analyze", cmd_analyze))
app_telegram.add_handler(CommandHandler("debug", cmd_debug))

# Audio file handlers (audio, voice, documents)
app_telegram.add_handler(
    MessageHandler(filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE, handle_audio)
)
app_telegram.add_handler(
    MessageHandler(filters.Document.ALL, handle_audio)  # Will check extension inside
)

# Text message handler (URLs, search queries, feedback)
app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# Catch-all for photos, stickers, etc.
app_telegram.add_handler(
    MessageHandler(
        filters.PHOTO | filters.Sticker.ALL | filters.VIDEO | filters.ANIMATION,
        handle_unsupported,
    )
)


# ── Startup ──────────────────────────────────────────────

if __name__ == "__main__":
    webhook_full = WEBHOOK_URL if WEBHOOK_URL.endswith("/webhook") else f"{WEBHOOK_URL}/webhook"

    logger.info("Starting MusicCheckBot webhook on port %s...", PORT)
    logger.info("Webhook URL: %s", webhook_full)

    # python-telegram-bot's run_webhook handles:
    # - Starting the HTTP server
    # - Setting the webhook with Telegram
    # - Managing the event loop properly
    # - Health check at /health (built-in)
    app_telegram.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="/webhook",
        webhook_url=webhook_full,
        drop_pending_updates=False,
    )
