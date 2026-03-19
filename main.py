"""MusicCheck Web — Flask web application for music analysis."""

import logging
import os
import tempfile
import time
import json

from flask import Flask, request, jsonify, render_template, send_from_directory, abort

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
RATE_LIMIT_CLEANUP_COUNTER = 0


def check_rate_limit(client_ip: str) -> bool:
    global RATE_LIMIT_CLEANUP_COUNTER
    now = time.time()
    hour_ago = now - 3600
    requests_list = [t for t in RATE_LIMITS.get(client_ip, []) if t > hour_ago]
    if len(requests_list) >= MAX_REQUESTS_PER_HOUR:
        return False
    RATE_LIMITS[client_ip] = requests_list + [now]

    # Cleanup old entries every 100 calls
    RATE_LIMIT_CLEANUP_COUNTER += 1
    if RATE_LIMIT_CLEANUP_COUNTER >= 100:
        RATE_LIMIT_CLEANUP_COUNTER = 0
        # Remove IP entries that have no recent requests
        ips_to_remove = [ip for ip, times in RATE_LIMITS.items() if not times or all(t <= hour_ago for t in times)]
        for ip in ips_to_remove:
            del RATE_LIMITS[ip]

    return True


# ── Search Cache ────────────────────────────────────────────
SEARCH_CACHE: dict[str, tuple[dict, float]] = {}
MAX_SEARCH_CACHE_SIZE = 100


def get_cached_search(query: str) -> dict | None:
    """Get cached search result if exists and less than 30 minutes old."""
    cache_key = query.lower().strip()
    if cache_key in SEARCH_CACHE:
        result, timestamp = SEARCH_CACHE[cache_key]
        if time.time() - timestamp < 1800:  # 30 minutes
            return result
        else:
            del SEARCH_CACHE[cache_key]
    return None


def set_cached_search(query: str, result: dict) -> None:
    """Store search result in cache with timestamp."""
    cache_key = query.lower().strip()
    if len(SEARCH_CACHE) >= MAX_SEARCH_CACHE_SIZE:
        # Remove oldest half
        items_to_remove = MAX_SEARCH_CACHE_SIZE // 2
        oldest = sorted(SEARCH_CACHE.items(), key=lambda x: x[1][1])[:items_to_remove]
        for key, _ in oldest:
            del SEARCH_CACHE[key]
    SEARCH_CACHE[cache_key] = (result, time.time())


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
        # Check cache first
        cached_result = get_cached_search(query)
        if cached_result:
            return jsonify(format_result_json(cached_result))

        result = analyze_from_text(query)
        # Store in cache after successful analysis
        if result and not result.get("error"):
            set_cached_search(query, result)
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
        fd, temp_path = tempfile.mkstemp(suffix=f".{ext}")
        os.close(fd)
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
            return jsonify({"identified": True, "track": result})
        else:
            return jsonify({"identified": False})
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
        fd, temp_path = tempfile.mkstemp(suffix=f".{ext}")
        os.close(fd)
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
    """Test all API connections (protected by DEBUG_KEY)."""
    # Check for DEBUG_KEY protection
    debug_key = os.getenv("DEBUG_KEY", "")
    query_key = request.args.get("key", "")

    # Allow if running locally or if key matches
    is_local = request.remote_addr in ("127.0.0.1", "localhost", "::1")
    if not is_local and (not debug_key or query_key != debug_key):
        return jsonify({"error": "unauthorized", "message": "Debug endpoint requires valid DEBUG_KEY"}), 403

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


def _bpm_desc(bpm):
    if bpm < 65: return "foarte lent, contemplativ"
    elif bpm < 85: return "lent, romantic, emotional"
    elif bpm < 100: return "ritm natural, nici prea rapid nici prea lent"
    elif bpm < 120: return "ritm moderat, flow natural"
    elif bpm < 135: return "ritm energic, bun pentru highlights"
    else: return "rapid, party vibe"

def _energy_desc(e):
    if e < 0.15: return "foarte calmă, intimă — perfectă pentru momente delicate"
    elif e < 0.3: return "moderată — nu-ți copleșește, lași emoția să iasă în față"
    elif e < 0.5: return "echilibrată — merge pe orice tip de montaj"
    elif e < 0.7: return "energică — bună pentru highlights și recap"
    else: return "intensă — party, dans, energie maximă"

def _key_desc(key, mode):
    descs = {
        "C": "ton cald, accesibil, feels good",
        "C#": "ton strălucitor, dramatic",
        "D": "ton luminos, optimist",
        "D#": "ton melancolic, profund",
        "E": "ton cald, plin de viață",
        "F": "ton pasional, romantic",
        "F#": "ton misterios, intens",
        "G": "ton vesel, natural",
        "G#": "ton dramatic, cinematografic",
        "A": "ton clar, sincer",
        "A#": "ton puternic, emotional",
        "B": "ton intim, vulnerabil",
    }
    base = descs.get(key, "ton neutru")
    if mode == "minor":
        base = base.replace("cald", "melancolic").replace("vesel", "nostalgic")
    return f"{key} {mode} — {base}"

def _valence_desc(v):
    if v < 0.15: return "trist, melancolie profundă"
    elif v < 0.3: return "melancolic, introspectiv"
    elif v < 0.5: return "neutru, versatil"
    elif v < 0.7: return "pozitiv, optimist"
    else: return "euforic, plin de bucurie"

def _wedding_score(features, is_explicit, thematic_issues):
    """Calculate wedding suitability score 0-100."""
    score = 80  # base
    if not features:
        return 50

    energy = features.get("energy", 0.5)
    valence = features.get("valence", 0.5)
    acousticness = features.get("acousticness", 0)
    instrumentalness = features.get("instrumentalness", 0)
    danceability = features.get("danceability", 0)

    # Positive
    if 0.3 <= valence <= 0.8: score += 5
    if acousticness > 0.5: score += 5
    if instrumentalness > 0.5: score += 5
    if 0.3 <= energy <= 0.7: score += 5
    if danceability > 0.4: score += 3

    # Negative
    if is_explicit: score -= 40
    if valence < 0.15: score -= 15
    if energy > 0.9: score -= 10

    high = [t for t in thematic_issues if "HIGH" in t.get("severity", "")]
    med = [t for t in thematic_issues if "MEDIUM" in t.get("severity", "")]
    score -= len(high) * 30
    score -= len(med) * 15

    return max(0, min(100, score))

def _video_placements(features, thematic_issues):
    """Generate detailed video placement suggestions."""
    if not features:
        return []

    bpm = features.get("tempo", 0)
    energy = features.get("energy", 0.5)
    valence = features.get("valence", 0.5)
    acousticness = features.get("acousticness", 0)
    instrumentalness = features.get("instrumentalness", 0)

    places = []

    high = any("HIGH" in t.get("severity", "") for t in thematic_issues)
    if high:
        return []

    if bpm < 85 or (energy < 0.3 and valence > 0.2):
        places.append({"name": "Ceremony", "desc": "vows, walking down the aisle, first kiss"})
    if energy < 0.5 and valence > 0.2:
        places.append({"name": "First Dance", "desc": "slow dance, romantic spotlight"})
    if valence > 0.3 or instrumentalness > 0.3:
        places.append({"name": "Trailer", "desc": "emotional hook, anticipation"})
    if 80 < bpm < 140:
        places.append({"name": "Highlights Reel", "desc": "flow natural peste toată nunta"})
    if energy < 0.6:
        places.append({"name": "Getting Ready", "desc": "intimate, real moments"})
    if bpm > 100 or energy > 0.5:
        places.append({"name": "Reception / Party", "desc": "softer moments, toasts, dance"})
    if instrumentalness > 0.5 or acousticness > 0.5:
        places.append({"name": "Cinematic Film", "desc": "full wedding film, montaj artistic"})

    return places[:6]


def format_result_json(result: dict) -> dict:
    """Format analysis result matching Telegram bot style."""

    # Error cases
    if result.get("error"):
        error_messages = {
            "not_identified": "Nu am putut identifica piesa. Încearcă să scrii numele artistului și piesei.",
            "not_found": f"Niciun rezultat pentru: {result.get('query', '')}. Încearcă altă ortografie.",
            "youtube_not_supported": "Link-urile YouTube nu sunt suportate încă. Scrie numele piesei.",
            "unsupported_url": "Acest tip de link nu e suportat. Încearcă un link Spotify sau scrie numele piesei.",
        }
        return {
            "error": result["error"],
            "message": error_messages.get(result["error"], f"Ceva nu a mers: {result['error']}")
        }

    track = result.get("track", {})
    features = result.get("features")
    lyrics_data = result.get("lyrics", {})
    explicit = result.get("explicit", {})
    wedding = result.get("wedding", {})
    cr = result.get("copyright", {})
    thematic = wedding.get("thematic", [])
    is_explicit = track.get("is_explicit", False)

    # Wedding score
    score = _wedding_score(features, is_explicit, thematic)

    # Quick verdict
    if score >= 80:
        quick_verdict = "DA, e PERFECT pentru nuntă! ✅✅"
    elif score >= 60:
        quick_verdict = "Merge cu atenție — verifică notele de mai jos ⚠️"
    elif score >= 40:
        quick_verdict = "Riscant — are probleme tematice sau de conținut ⚠️"
    else:
        quick_verdict = "NU e recomandat pentru nuntă ❌"

    # Mood & Vibe
    mood_items = []
    if features:
        bpm = features.get("tempo", 0)
        energy = features.get("energy", 0.5)
        valence = features.get("valence", 0.5)
        key = features.get("key", "?")
        mode = features.get("mode", "")
        acousticness = features.get("acousticness", 0)
        danceability = features.get("danceability", 0)

        # Overall mood
        if valence > 0.6 and energy > 0.5:
            mood_items.append("Optimist, hopeful, inspirational")
        elif valence > 0.5 and energy < 0.4:
            mood_items.append("Calm, romantic, dreamy")
        elif valence < 0.3 and energy < 0.3:
            mood_items.append("Melancolic, introspectiv, profund")
        elif valence < 0.3 and energy > 0.5:
            mood_items.append("Dramatic, intens, cinematic")
        elif energy > 0.7:
            mood_items.append("Energic, party, celebrare")
        else:
            mood_items.append("Echilibrat, versatil, natural")

        mood_items.append(f"{int(bpm)} BPM — {_bpm_desc(bpm)}")
        mood_items.append(f"Energie {energy:.2f} — {_energy_desc(energy)}")
        mood_items.append(_key_desc(key, mode))

        if danceability > 0.6:
            mood_items.append(f"Danceability ridicată ({danceability:.2f}) — ritm contagios")
        if acousticness > 0.5:
            mood_items.append(f"Acustic ({acousticness:.2f}) — ton organic, natural")

    # Video placements
    placements = _video_placements(features, thematic)

    # Copyright details
    copyright_details = []
    label_name = cr.get("label", "Unknown")
    if label_name and label_name != "Unknown":
        copyright_details.append(f"Label: {label_name}")
    copyright_details.append(cr.get("note", ""))

    copyright_actions = []
    if "High" in cr.get("risk", ""):
        copyright_actions = [
            "E pe Artlist sau Epidemic Sound? (royalty-free libraries)",
            "E pe DistroKid? (independent artist)",
            "Are Creative Commons license?",
            "Dacă e din stock music — care-i licența exactă?",
        ]
        copyright_warning = "NU o folosi pe Instagram/YouTube public fără să știi licensing-ul."
    elif "Medium" in cr.get("risk", ""):
        copyright_actions = [
            "Verifică dacă e royalty-free sau licențiat",
            "Caută pe Artlist, Epidemic Sound, sau Musicbed",
        ]
        copyright_warning = "Verifică înainte de a publica pe social media."
    else:
        copyright_actions = []
        copyright_warning = ""

    # Final verdict
    if score >= 80:
        final_music = f"Muzical? PERFECT. {score}/100. 🎵"
    elif score >= 60:
        final_music = f"Muzical? Bun cu rezerve. {score}/100."
    else:
        final_music = f"Muzical? Problematic. {score}/100."

    if "High" in cr.get("risk", "") or "Medium" in cr.get("risk", ""):
        final_legal = "Legal? NECUNOSCUT — verifică acum!"
    else:
        final_legal = "Legal? Risc scăzut — probabil safe."

    # Lyrics preview
    lyrics_preview = None
    lyrics_text = lyrics_data.get("lyrics")
    if lyrics_text:
        preview_lines = [l for l in lyrics_text.split("\n") if l.strip()][:6]
        lyrics_preview = "\n".join(preview_lines)

    return {
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
        "header": {
            "bpm": int(features.get("tempo", 0)) if features else None,
            "key": f"{features.get('key', '?')} {features.get('mode', '')}" if features else None,
            "score": score,
            "copyright_status": cr.get("risk", "?"),
        },
        "quick_verdict": quick_verdict,
        "mood_items": mood_items,
        "placements": placements,
        "thematic": thematic,
        "copyright": {
            "risk": cr.get("risk", "?"),
            "details": copyright_details,
            "actions": copyright_actions,
            "warning": copyright_warning,
            "label": label_name,
        },
        "explicit": explicit.get("label", "✅ Clean"),
        "final_verdict": {
            "music": final_music,
            "legal": final_legal,
            "score": score,
        },
        "lyrics": {
            "preview": lyrics_preview,
            "url": lyrics_data.get("url"),
            "source": lyrics_data.get("source", "none"),
        },
    }


# ═══════════════════════════════════════════════════════════
# SECURITY & ERROR HANDLERS
# ═══════════════════════════════════════════════════════════


@app.after_request
def add_security_headers(response):
    """Add security and CORS headers to all responses."""
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # CORS headers for API routes
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    return response


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle request entity too large (file too big)."""
    return jsonify({
        "error": "file_too_large",
        "message": f"File exceeds maximum size of {MAX_FILE_SIZE_MB}MB"
    }), 413


# ═══════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting MusicCheck Web on port %s...", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
