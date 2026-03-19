"""Track search & metadata — MusicBrainz (free, no auth) + optional Spotify fallback.
Spotify Audio Features API was deprecated Feb 2026 for Development Mode apps.
We now use MusicBrainz for search and librosa for audio analysis."""

import logging
import re
import time

import musicbrainzngs
import requests

from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

logger = logging.getLogger(__name__)

# ── MusicBrainz setup ────────────────────────────────────
musicbrainzngs.set_useragent("MusicCheckBot", "1.0", "atomm355@gmail.com")

# ── Spotify token (kept as optional enrichment) ──────────
_token_cache = {"token": None, "expires_at": 0}
API_TIMEOUT = 12
MAX_RETRIES = 1


def _get_spotify_token() -> str | None:
    """Get Spotify token — optional, may fail on cloud hosting."""
    global _token_cache
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            _token_cache["token"] = data["access_token"]
            _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
            return _token_cache["token"]
    except Exception as e:
        logger.warning("Spotify token failed (expected on cloud): %s", e)
    return None


# ═══════════════════════════════════════════════════════════
# PRIMARY: MusicBrainz search (free, no auth, no rate issues)
# ═══════════════════════════════════════════════════════════

def search_track(query: str) -> dict | None:
    """Search for a track — MusicBrainz primary, Spotify fallback."""
    # Try MusicBrainz first (always works)
    result = _search_musicbrainz(query)
    if result:
        return result

    # Spotify fallback (may fail on cloud)
    result = _search_spotify(query)
    if result:
        return result

    return None


def _search_musicbrainz(query: str) -> dict | None:
    """Search MusicBrainz for a track."""
    try:
        result = musicbrainzngs.search_recordings(query=query, limit=1)
        recordings = result.get("recording-list", [])
        if not recordings:
            return None

        rec = recordings[0]
        title = rec.get("title", "Unknown")
        artists = ", ".join(
            a.get("name", "") for a in rec.get("artist-credit", [])
            if isinstance(a, dict) and a.get("name")
        )
        if not artists:
            artists = "Unknown"

        # Get release info
        releases = rec.get("release-list", [])
        album = ""
        release_date = ""
        label = ""
        if releases:
            rel = releases[0]
            album = rel.get("title", "")
            release_date = rel.get("date", "")
            # Try to get label
            label_list = rel.get("label-info-list", [])
            if label_list and label_list[0].get("label"):
                label = label_list[0]["label"].get("name", "")

        isrc = ""
        isrc_list = rec.get("isrc-list", [])
        if isrc_list:
            isrc = isrc_list[0]

        # Duration
        duration_ms = int(rec.get("length", 0) or 0)
        minutes = duration_ms // 60000
        seconds = (duration_ms % 60000) // 1000
        duration = f"{minutes}:{seconds:02d}" if duration_ms > 0 else ""

        return {
            "title": title,
            "artist": artists,
            "album": album,
            "release_date": release_date,
            "duration": duration,
            "duration_ms": duration_ms,
            "is_explicit": False,  # MusicBrainz doesn't track explicit
            "spotify_id": "",
            "spotify_url": "",
            "label": label,
            "copyrights": [],
            "popularity": 0,
            "isrc": isrc,
            "source": "MusicBrainz",
        }
    except Exception as e:
        logger.error("MusicBrainz search error: %s", e)
        return None


def _search_spotify(query: str) -> dict | None:
    """Spotify search fallback — may not work on cloud hosting."""
    token = _get_spotify_token()
    if not token:
        return None
    try:
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "type": "track", "limit": 1},
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 200:
            items = resp.json().get("tracks", {}).get("items", [])
            if items:
                return _extract_spotify_track(items[0])
    except Exception as e:
        logger.warning("Spotify search failed: %s", e)
    return None


# ═══════════════════════════════════════════════════════════
# Track by ID (Spotify only — used when ACRCloud returns spotify_id)
# ═══════════════════════════════════════════════════════════

def get_track_by_id(track_id: str) -> dict | None:
    """Get track info by Spotify track ID."""
    token = _get_spotify_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/tracks/{track_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 200:
            return _extract_spotify_track(resp.json())
    except Exception as e:
        logger.warning("Spotify get_track failed: %s", e)
    return None


def get_audio_features(track_id: str) -> dict | None:
    """Get Spotify audio features — DEPRECATED Feb 2026 for Dev Mode apps.
    Returns None on cloud hosting. librosa handles this now."""
    token = _get_spotify_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/audio-features/{track_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and data.get("tempo"):
                key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
                key_idx = data.get("key", -1)
                mode = data.get("mode", 0)
                return {
                    "tempo": round(data.get("tempo", 0), 1),
                    "key": key_names[key_idx] if 0 <= key_idx <= 11 else "Unknown",
                    "mode": "major" if mode == 1 else "minor",
                    "energy": round(data.get("energy", 0), 3),
                    "danceability": round(data.get("danceability", 0), 3),
                    "valence": round(data.get("valence", 0), 3),
                    "acousticness": round(data.get("acousticness", 0), 3),
                    "instrumentalness": round(data.get("instrumentalness", 0), 3),
                    "speechiness": round(data.get("speechiness", 0), 3),
                    "loudness": round(data.get("loudness", 0), 1),
                    "time_signature": data.get("time_signature", 4),
                    "source": "Spotify",
                }
        elif resp.status_code == 403:
            logger.info("Spotify audio features 403 — deprecated for Dev Mode apps")
    except Exception as e:
        logger.warning("Spotify audio features failed: %s", e)
    return None


def extract_spotify_id_from_url(url: str) -> str | None:
    """Extract Spotify track ID from a Spotify URL."""
    match = re.search(r"spotify\.com/track/([a-zA-Z0-9]+)", url)
    return match.group(1) if match else None


def _extract_spotify_track(track: dict) -> dict:
    """Extract standardized track info from Spotify track object."""
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    album = track.get("album", {})
    duration_ms = track.get("duration_ms", 0)
    minutes = duration_ms // 60000
    seconds = (duration_ms % 60000) // 1000

    return {
        "title": track.get("name", "Unknown"),
        "artist": artists,
        "album": album.get("name", "Unknown"),
        "release_date": album.get("release_date", ""),
        "duration": f"{minutes}:{seconds:02d}",
        "duration_ms": duration_ms,
        "is_explicit": track.get("explicit", False),
        "spotify_id": track.get("id", ""),
        "spotify_url": track.get("external_urls", {}).get("spotify", ""),
        "label": album.get("label", ""),
        "copyrights": album.get("copyrights", []),
        "popularity": track.get("popularity", 0),
        "source": "Spotify",
    }
