"""Telegram message handlers — all bot commands and message types."""

import logging
import os
import re
import tempfile
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from analyzer import (
    analyze_from_audio,
    analyze_from_text,
    analyze_from_url,
    quick_identify_audio,
)
from audio_utils import cleanup_temp_files, get_audio_duration
from config import (
    ADMIN_CHAT_ID,
    ALLOWED_AUDIO_FORMATS,
    MAX_FILE_SIZE_MB,
    MAX_REQUESTS_PER_HOUR,
    MIN_AUDIO_DURATION_SEC,
)
from formatter import (
    format_about,
    format_analysis,
    format_help,
    format_quick_id,
    format_welcome,
)

logger = logging.getLogger(__name__)

# ── Rate limiting (in-memory) ────────────────────────────
USER_LIMITS: dict[int, list[float]] = {}


def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    hour_ago = now - 3600
    requests = [t for t in USER_LIMITS.get(user_id, []) if t > hour_ago]
    if len(requests) >= MAX_REQUESTS_PER_HOUR:
        return False
    USER_LIMITS[user_id] = requests + [now]
    return True


# ── Command handlers ─────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_welcome())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_help())


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_about())


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 Got feedback? Just type your message here and I'll forward it to the developer.\n\n"
        "Or email: atomm355@gmail.com"
    )
    context.user_data["awaiting_feedback"] = True


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command — test all API connections."""
    import time as _time
    lines = ["🔧 API Connection Test\n"]

    # Test MusicBrainz (primary search)
    try:
        from spotify_client import search_track
        t0 = _time.time()
        result = search_track("Ed Sheeran Perfect")
        elapsed = _time.time() - t0
        if result:
            lines.append(f"✅ Search: OK ({elapsed:.1f}s) — '{result.get('artist')} - {result.get('title')}' via {result.get('source')}")
        else:
            lines.append(f"❌ Search: No results ({elapsed:.1f}s)")
    except Exception as e:
        elapsed = _time.time() - t0
        lines.append(f"❌ Search: {str(e)[:100]} ({elapsed:.1f}s)")

    # Test Genius
    try:
        from config import GENIUS_ACCESS_TOKEN
        if GENIUS_ACCESS_TOKEN:
            lines.append(f"✅ Genius: Token configured ({GENIUS_ACCESS_TOKEN[:8]}...)")
        else:
            lines.append("❌ Genius: No token")
    except Exception as e:
        lines.append(f"❌ Genius: {e}")

    # Test ACRCloud
    try:
        from config import ACRCLOUD_ACCESS_KEY
        if ACRCLOUD_ACCESS_KEY:
            lines.append(f"✅ ACRCloud: Key configured ({ACRCLOUD_ACCESS_KEY[:8]}...)")
        else:
            lines.append("❌ ACRCloud: No key")
    except Exception as e:
        lines.append(f"❌ ACRCloud: {e}")

    # Test AudD
    try:
        from config import AUDD_API_TOKEN
        if AUDD_API_TOKEN:
            lines.append(f"✅ AudD: Token configured ({AUDD_API_TOKEN[:8]}...)")
        else:
            lines.append("❌ AudD: No token")
    except Exception as e:
        lines.append(f"❌ AudD: {e}")

    # Show env info
    import os
    lines.append(f"\n📋 PORT={os.getenv('PORT','?')}")
    lines.append(f"SPOTIFY_CLIENT_ID={os.getenv('SPOTIFY_CLIENT_ID','')[:8]}...")

    await update.message.reply_text("\n".join(lines))


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyze command with text search."""
    user_id = update.effective_user.id

    if not check_rate_limit(user_id):
        await update.message.reply_text(
            "⏳ You've hit the rate limit (20 analyses per hour). Try again shortly."
        )
        return

    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "Usage: /analyze Artist - Song Title\n"
            "Example: /analyze Ed Sheeran Perfect"
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    await update.message.reply_text(f"🔍 Searching for: {query}...")

    try:
        result = analyze_from_text(query)
        response = format_analysis(result)
        await _send_long_message(update, response)
    except Exception as e:
        logger.error("Analyze command error: %s", e)
        await update.message.reply_text(
            "⚠️ Something went wrong during analysis. Please try again."
        )
        await _notify_admin(context, f"Error in /analyze: {e}")


# ── Audio file handler ───────────────────────────────────


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded audio files and voice messages — two-phase response."""
    user_id = update.effective_user.id

    if not check_rate_limit(user_id):
        await update.message.reply_text(
            "⏳ You've hit the rate limit (20 analyses per hour). Try again shortly."
        )
        return

    # Get the file object
    audio_file = None
    file_name = "audio"

    if update.message.audio:
        audio_file = update.message.audio
        file_name = audio_file.file_name or "audio.mp3"
    elif update.message.voice:
        audio_file = update.message.voice
        file_name = "voice.ogg"
    elif update.message.document:
        doc = update.message.document
        ext = (doc.file_name or "").rsplit(".", 1)[-1].lower()
        if ext in ALLOWED_AUDIO_FORMATS:
            audio_file = doc
            file_name = doc.file_name or f"document.{ext}"
        else:
            await update.message.reply_text(
                f"⚠️ Unsupported file type: .{ext}\n\n"
                f"Supported formats: {', '.join(sorted(ALLOWED_AUDIO_FORMATS))}"
            )
            return
    elif update.message.video_note:
        audio_file = update.message.video_note
        file_name = "videonote.mp4"

    if not audio_file:
        return

    # Check file size
    file_size_mb = (audio_file.file_size or 0) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        await update.message.reply_text(
            f"⚠️ File too large ({file_size_mb:.1f}MB). Maximum is {MAX_FILE_SIZE_MB}MB."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    status_msg = await update.message.reply_text("🎵 Identifying your track...")

    temp_path = None
    try:
        # Download file
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "mp3"
        temp_path = tempfile.mktemp(suffix=f".{ext}")
        tg_file = await audio_file.get_file()
        await tg_file.download_to_drive(temp_path)

        # Check duration
        duration = get_audio_duration(temp_path)
        if 0 < duration < MIN_AUDIO_DURATION_SEC:
            await update.message.reply_text(
                f"⚠️ Audio too short ({duration:.1f}s). "
                f"Need at least {MIN_AUDIO_DURATION_SEC} seconds for reliable identification."
            )
            return

        # ═══ PHASE 1: Quick identification ═══
        await update.message.chat.send_action(ChatAction.TYPING)
        quick_result = quick_identify_audio(temp_path)

        if quick_result and not quick_result.get("error"):
            # Send quick ID immediately
            quick_msg = format_quick_id(quick_result)
            await update.message.reply_text(quick_msg)

            # ═══ PHASE 2: Full analysis ═══
            await update.message.chat.send_action(ChatAction.TYPING)
            try:
                await status_msg.edit_text("📊 Running full analysis...")
            except Exception:
                pass

            result = analyze_from_audio(temp_path, preloaded_track=quick_result)
            response = format_analysis(result)
            await _send_long_message(update, response)
        else:
            # No identification — try full pipeline anyway
            try:
                await status_msg.edit_text("🔍 Couldn't identify quickly — running deep scan...")
            except Exception:
                pass

            result = analyze_from_audio(temp_path)
            response = format_analysis(result)
            await _send_long_message(update, response)

    except Exception as e:
        logger.error("Audio handler error: %s", e)
        await update.message.reply_text(
            "⚠️ Couldn't process this audio file. Try a different format or use:\n"
            "/analyze Artist - Song Title"
        )
        await _notify_admin(context, f"Audio handler error: {e}")
    finally:
        cleanup_temp_files(temp_path)


# ── Text message handler ─────────────────────────────────


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages (URLs, feedback, or search queries)."""
    text = update.message.text.strip()

    # Check if awaiting feedback
    if context.user_data.get("awaiting_feedback"):
        context.user_data["awaiting_feedback"] = False
        await update.message.reply_text("✅ Thanks for the feedback! Forwarded to the developer.")
        await _notify_admin(
            context,
            f"📬 Feedback from {update.effective_user.first_name} "
            f"(@{update.effective_user.username or 'no username'}):\n\n{text}",
        )
        return

    # Check for Spotify URL
    if "spotify.com/track/" in text:
        user_id = update.effective_user.id
        if not check_rate_limit(user_id):
            await update.message.reply_text("⏳ Rate limit reached. Try again shortly.")
            return

        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text("🔗 Spotify link detected — analyzing...")

        try:
            result = analyze_from_url(text)
            response = format_analysis(result)
            await _send_long_message(update, response)
        except Exception as e:
            logger.error("URL handler error: %s", e)
            await update.message.reply_text("⚠️ Couldn't analyze this link. Try /analyze instead.")
        return

    # Check for YouTube URL
    if re.search(r"(?:youtube\.com/watch|youtu\.be/)", text):
        await update.message.reply_text(
            "🔗 YouTube link support coming soon!\n\n"
            "For now, type the artist and song name:\n"
            "/analyze Artist - Song Title"
        )
        return

    # Otherwise, treat as a search query
    if len(text) > 3:
        user_id = update.effective_user.id
        if not check_rate_limit(user_id):
            await update.message.reply_text("⏳ Rate limit reached. Try again shortly.")
            return

        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text(f"🔍 Searching for: {text}...")

        try:
            result = analyze_from_text(text)
            response = format_analysis(result)
            await _send_long_message(update, response)
        except Exception as e:
            logger.error("Text search error: %s", e)
            await update.message.reply_text("⚠️ Search failed. Try: /analyze Artist - Song Title")
    else:
        await update.message.reply_text(
            "Send me an audio file, a Spotify link, or type a song name to analyze.\n\n"
            "Need help? /help"
        )


# ── Utilities ────────────────────────────────────────────


async def _send_long_message(update: Update, text: str, max_len: int = 4096):
    """Send a message, splitting if it exceeds Telegram's limit."""
    if len(text) <= max_len:
        await update.message.reply_text(text)
    else:
        parts = []
        while text:
            if len(text) <= max_len:
                parts.append(text)
                break
            # Find a good split point
            split_at = text.rfind("\n", 0, max_len)
            if split_at < max_len // 2:
                split_at = max_len
            parts.append(text[:split_at])
            text = text[split_at:].lstrip("\n")

        for part in parts:
            await update.message.reply_text(part)


async def _notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send error notification to admin."""
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID), text=f"🤖 Bot Alert:\n{message[:3000]}"
            )
        except Exception:
            pass


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle unsupported file types."""
    await update.message.reply_text(
        "⚠️ I can only analyze audio files.\n\n"
        f"Supported formats: {', '.join(sorted(ALLOWED_AUDIO_FORMATS))}\n\n"
        "Or type: /analyze Artist - Song Title"
    )
