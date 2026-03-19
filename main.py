"""MusicCheck Web — Flask web application for music analysis."""

import logging
import os
import tempfile
import time
import json

from flask import Flask, request, jsonify, render_template, send_from_directory

from config import (
    PORT,
    SECRET_KEY,
    ALLOWED_AUDIO_FORMATS,
    MAX_FILE_SIZE_MB,
    MAX_REQUESTS_PER_HOUR,
    MIN_AUDIO_DURATION_SEC,
)
from analyzer import (
    analyze_from_audio,
    analyze_from_text,
    analyze_from_url,
    quick_identify_audio,
)
from audio_utils import cleanup_temp_files, get_audio_duration

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Flask App ────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_MB * 1024 * 1024

# ── Rate Limiting (in-memory, per IP) ────────────────────
RATE_LIMITS: dict[str, list[float]] = {}


def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    hour_ago = now - 3600
    requests_list = [t for t in RATE_LIMITS.get(client_ip, []) if t > hour_ago]
    if len(requests_list) >= MAX_REQUESTS_PER_HOUR:
        return False
    RATE_LIMITS[client_ip] = requests_list + [now]
    return True


# ═══════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════


@app.route("/")
def index():
    """Serve the main web interface."""
    return render_template("index.html")


@app.route("/health")
def health():
    """Health check for Render."""
    return jsonify({"status": "ok", "service": "MusicCheck Web"})


@app.route("/api/analyze/text", methods=["POST"])
def api_analyze_text():
    """Analyze a track by text search query."""
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        return jsonify({"error": "rate_limit", "message": "Too many requests. Try again in a few minutes."}), 429

    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()

    if not query or len(query) < 3:
        return jsonify({"error": "invalid_query", "message": "Please enter at least 3 characters."}), 400

    try:
        result = analyze_from_text(query)
        return jsonify(format_result_json(result))
    except Exception as e:
        logger.error("Text analysis error: %s", e)
        return jsonify({"error": "analysis_failed", "message": "Analysis failed. Please try again."}), 500


@app.route("/api/analyze/url", methods=["POST"])
def api_analyze_url():
    """Analyze a track from a Spotify URL."""
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        return jsonify({"error": "rate_limit", "message": "Too many requests. Try again in a few minutes."}), 429

    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "invalid_url", "message": "Please provide a URL."}), 400

    try:
        result = analyze_from_url(url)
        return jsonify(format_result_json(result))
    except Exception as e:
        logger.error("URL analysis error: %s", e)
        return jsonify({"error": "analysis_failed", "message": "Analysis failed. Please try again."}), 500


@app.route("/api/identify", methods=["POST"])
def api_identify():
    """Quick audio identification only (Phase 1)."""
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        return jsonify({"error": "rate_limit", "message": "Too many requests."}), 429

    if "audio" not in request.files:
        return jsonify({"error": "no_file", "message": "No audio file provided."}), 400

    audio_file = request.files["audio"]
    filename = audio_file.filename or "audio.mp3"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ALLOWED_AUDIO_FORMATS:
        return jsonify({
            "error": "unsupported_format",
            "message": f"Unsupported format: .{ext}. Use: {', '.join(sorted(ALLOWED_AUDIO_FORMATS))}"
        }), 400

    temp_path = None
    try:
        temp_path = tempfile.mktemp(suffix=f".{ext}")
        audio_file.save(temp_path)

        # Check duration
        duration = get_audio_duration(temp_path)
        if 0 < duration < MIN_AUDIO_DURATION_SEC:
            return jsonify({
                "error": "too_short",
                "message": f"Audio too short ({duration:.1f}s). Need at least {MIN_AUDIO_DURATION_SEC}s."
            }), 400

        result = quick_identify_audio(temp_path)
        if result and not result.get("error"):
            return jsonify({"identified": True, "track": result, "temp_path": temp_path})
        else:
            return jsonify({"identified": False, "temp_path": temp_path})
    except Exception as e:
        logger.error("Identify error: %s", e)
        cleanup_temp_files(temp_path)
        return jsonify({"error": "identify_failed", "message": "Could not process audio file."}), 500


@app.route("/api/analyze/audio", methods=["POST"])
def api_analyze_audio():
    """Full audio analysis (Phase 2, or standalone)."""
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        return jsonify({"error": "rate_limit", "message": "Too many requests."}), 429

    if "audio" not in request.files:
        return jsonify({"error": "no_file", "message": "No audio file provided."}), 400

    audio_file = request.files["audio"]
    filename = audio_file.filename or "audio.mp3"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ALLOWED_AUDIO_FORMATS:
        return jsonify({
            "error": "unsupported_format",
            "message": f"Unsupported format: .{ext}. Use: {', '.join(sorted(ALLOWED_AUDIO_FORMATS))}"
        }), 400

    temp_path = None
    try:
        temp_path = tempfile.mktemp(suffix=f".{ext}")
        audio_file.save(temp_path)

        # Check duration
        duration = get_audio_duration(temp_path)
        if 0 < duration < MIN_AUDIO_DURATION_SEC:
            return jsonify({
                "error": "too_short",
                "message": f"Audio too short ({duration:.1f}s). Need at least {MIN_AUDIO_DURATION_SEC}s."
            }), 400

        result = analyze_from_audio(temp_path)
        return jsonify(format_result_json(result))
    except Exception as e:
        logger.error("Audio analysis error: %s", e)
        return jsonify({"error": "analysis_failed", "message": "Analysis failed. Please try again."}), 500
    finally:
        cleanup_temp_files(temp_path)


@app.route("/api/debug", methods=["GET"])
def api_debug():
    """Test all API connections."""
    lines = []

    # Test MusicBrainz
    try:
        from spotify_client import search_track
        t0 = time.time()
        result = search_track("Ed Sheeran Perfect")
        elapsed = time.time() - t0
        if result:
            lines.append({"service": "Search (MusicBrainz)", "status": "ok", "detail": f"{result.get('artist')} - {result.get('title')} via {result.get('source')}", "time": f"{elapsed:.1f}s"})
        else:
            lines.append({"service": "Search", "status": "fail", "detail": "No results", "time": f"{elapsed:.1f}s"})
    except Exception as e:
        lines.append({"service": "Search", "status": "error", "detail": str(e)[:100]})

    # Test Genius
    from config import GENIUS_ACCESS_TOKEN
    lines.append({
        "service": "Genius",
        "status": "ok" if GENIUS_ACCESS_TOKEN else "missing",
        "detail": f"Token: {GENIUS_ACCESS_TOKEN[:8]}..." if GENIUS_ACCESS_TOKEN else "No token configured"
    })

    # Test ACRCloud
    from config import ACRCLOUD_ACCESS_KEY
    lines.append({
        "service": "ACRCloud",
        "status": "ok" if ACRCLOUD_ACCESS_KEY else "missing",
        "detail": f"Key: {ACRCLOUD_ACCESS_KEY[:8]}..." if ACRCLOUD_ACCESS_KEY else "No key configured"
    })

    # Test AudD
    from config import AUDD_API_TOKEN
    lines.append({
        "service": "AudD",
        "status": "ok" if AUDD_API_TOKEN else "missing",
        "detail": f"Token: {AUDD_API_TOKEN[:8]}..." if AUDD_API_TOKEN else "No token configured"
    })

    return jsonify({"checks": lines})


# ═══════════════════════════════════════════════════════════
# JSON FORMATTER (replaces Telegram formatter)
# ═══════════════════════════════════════════════════════════


def _format_bpm_label(bpm):
    if bpm < 70: return "slow"
    elif bpm < 100: return "medium"
    elif bpm < 130: return "fast"
    else: return "very fast"


def _format_energy_label(energy):
    if energy < 0.25: return "calm"
    elif energy < 0.5: return "moderate"
    elif energy < 0.75: return "energetic"
    else: return "intense"


def _format_valence_label(valence):
    if valence < 0.15: return "sad"
    elif valence < 0.35: return "melancholic"
    elif valence < 0.55: return "neutral"
    elif valence < 0.75: return "positive"
    else: return "euphoric"


def format_result_json(result: dict) -> dict:
    """Format analysis result as structured JSON for the web frontend."""

    # Error cases
    if result.get("error"):
        error_messages = {
            "not_identified": "Couldn't identify this track. Try typing the artist and song name instead.",
            "not_found": f"No results found for: {result.get('query', '')}. Try a different spelling.",
            "youtube_not_supported": "YouTube link support coming soon. Type the song name instead.",
            "unsupported_url": "This link type isn't supported. Try a Spotify link or type the song name.",
        }
        return {
            "error": result["error"],
            "message": error_messages.get(result["error"], f"Something went wrong: {result['error']}")
        }

    track = result.get("track", {})
    features = result.get("features")
    lyrics_data = result.get("lyrics", {})
    explicit = result.get("explicit", {})
    wedding = result.get("wedding", {})
    cr = result.get("copyright", {})

    # Build structured response
    response = {
        "success": True,
        "track": {
            "title": track.get("title", "Unknown"),
            "artist": track.get("artist", "Unknown"),
            "album": track.get("album", ""),
            "release_date": track.get("release_date", ""),
            "duration": track.get("duration", ""),
            "spotify_url": track.get("spotify_url", ""),
            "source": track.get("source", ""),
        },
        "features": None,
        "verdict": {
            "wedding": wedding.get("verdict", "Unknown"),
            "explicit": explicit.get("label", "Unknown"),
            "explicit_detail": explicit.get("detail", ""),
            "copyright_risk": cr.get("risk", "Unknown"),
            "copyright_note": cr.get("note", ""),
            "copyright_label": cr.get("label", "Unknown"),
            "copyright_disclaimer": cr.get("disclaimer", ""),
            "placement": wedding.get("placement", []),
            "issues": wedding.get("issues", []),
        },
        "thematic": wedding.get("thematic", []),
        "lyrics": {
            "preview": None,
            "url": lyrics_data.get("url"),
            "source": lyrics_data.get("source", "none"),
        },
    }

    # Audio features
    if features:
        bpm = features.get("tempo", 0)
        energy = features.get("energy", 0)
        valence = features.get("valence", 0)
        response["features"] = {
            "bpm": bpm,
            "bpm_label": _format_bpm_label(bpm),
            "key": features.get("key", "?"),
            "mode": features.get("mode", ""),
            "energy": round(energy * 100),
            "energy_label": _format_energy_label(energy),
            "danceability": round(features.get("danceability", 0) * 100),
            "valence": round(valence * 100),
            "valence_label": _format_valence_label(valence),
            "acousticness": round(features.get("acousticness", 0) * 100),
            "instrumentalness": round(features.get("instrumentalness", 0) * 100),
            "source": features.get("source", ""),
        }

    # Lyrics preview
    lyrics_text = lyrics_data.get("lyrics")
    if lyrics_text:
        preview_lines = [l for l in lyrics_text.split("\n") if l.strip()][:6]
        response["lyrics"]["preview"] = "\n".join(preview_lines)

    return response


# ═══════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting MusicCheck Web on port %s...", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
