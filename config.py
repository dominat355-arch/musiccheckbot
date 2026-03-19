"""Configuration — loads all environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Music Recognition ────────────────────────────────────
ACRCLOUD_HOST = os.getenv("ACRCLOUD_HOST", "identify-eu-west-1.acrcloud.com")
ACRCLOUD_ACCESS_KEY = os.getenv("ACRCLOUD_ACCESS_KEY", "")
ACRCLOUD_ACCESS_SECRET = os.getenv("ACRCLOUD_ACCESS_SECRET", "")
AUDD_API_TOKEN = os.getenv("AUDD_API_TOKEN", "")

# ── Spotify ──────────────────────────────────────────────
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

# ── Lyrics ───────────────────────────────────────────────
GENIUS_ACCESS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN", "")

# ── Rate Limits ──────────────────────────────────────────
MAX_REQUESTS_PER_HOUR = int(os.getenv("MAX_REQUESTS_PER_HOUR", "60"))
MAX_FILE_SIZE_MB = 20
MIN_AUDIO_DURATION_SEC = 5
ALLOWED_AUDIO_FORMATS = {"mp3", "m4a", "wav", "ogg", "flac", "aac", "oga"}

# ── Server ───────────────────────────────────────────────
PORT = int(os.getenv("PORT", "8000"))
SECRET_KEY = os.getenv("SECRET_KEY", "musiccheck-web-secret-key-change-me")
