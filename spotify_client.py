"""Spotify API integration — track search, audio features, explicit flag.
Uses direct requests instead of spotipy for better timeout control on Railway."""

import logging
import re
import time

import requests

from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

logger = logging.getLogger(__name__)

_token_cache = {"token": None, "expires_at": 0}

API_TIMEOUT = 15  # per request timeout
MAX_RETRIES = 2


def _get_token() -> str | None:
    """Get a valid Spotify access token using Client Credentials flow."""
    global _token_cache

    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        logger.warning("Spotify credentials not configured.")
        return None

    print(f"[SPOTIFY] Getting token... client_id={SPOTIFY_CLIENT_ID[:8]}..., secret={SPOTIFY_CLIENT_SECRET[:4]}...")
    for attempt in range(MAX_RETRIES + 1):
        try:
            print(f"[SPOTIFY] Auth attempt {attempt+1}/{MAX_RETRIES+1}")
            resp = requests.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
                timeout=API_TIMEOUT,
            )
            print(f"[SPOTIFY] Auth response: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                _token_cache["token"] = data["access_token"]
                _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
                logger.info("Spotify token acquired (attempt %d)", attempt + 1)
                return _token_cache["token"]
            else:
                logger.error("Spotify auth failed: %s %s", resp.status_code, resp.text[:200])
                return None
        except requests.Timeout:
            print(f"[SPOTIFY] Auth TIMEOUT (attempt {attempt+1}/{MAX_RETRIES+1})")
            logger.warning("Spotify auth timeout (attempt %d/%d)", attempt + 1, MAX_RETRIES + 1)
            if attempt < MAX_RETRIES:
                time.sleep(1)
                continue
            return None
        except Exception as e:
            logger.error("Spotify auth error: %s", e)
            return None

    return None


def _api_get(endpoint: str, params: dict = None) -> dict | None:
    """Make a GET request to Spotify API with retry logic."""
    token = _get_token()
    if not token:
        return None

    url = f"https://api.spotify.com/v1{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=API_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                # Token expired, refresh and retry
                _token_cache["token"] = None
                token = _get_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                    continue
                return None
            else:
                logger.error("Spotify API %s: %s", resp.status_code, resp.text[:200])
                return None
        except requests.Timeout:
            logger.warning("Spotify API timeout %s (attempt %d/%d)", endpoint, attempt + 1, MAX_RETRIES + 1)
            if attempt < MAX_RETRIES:
                time.sleep(1)
                continue
            return None
        except Exception as e:
            logger.error("Spotify API error: %s", e)
            return None

    return None


def search_track(query: str) -> dict | None:
    """Search Spotify for a track by text query. Returns track info dict."""
    print(f"[SPOTIFY] search_track called with: {query}")
    data = _api_get("/search", {"q": query, "type": "track", "limit": 1})
    print(f"[SPOTIFY] search_track result: {type(data)} - {str(data)[:100] if data else 'None'}")
    if not data:
        return None

    items = data.get("tracks", {}).get("items", [])
    if not items:
        return None

    return _extract_track_info(items[0])


def get_track_by_id(track_id: str) -> dict | None:
    """Get track info by Spotify track ID."""
    data = _api_get(f"/tracks/{track_id}")
    if not data:
        return None
    return _extract_track_info(data)


def get_audio_features(track_id: str) -> dict | None:
    """Get audio features (BPM, key, energy, etc.) for a Spotify track."""
    data = _api_get(f"/audio-features/{track_id}")
    if not data:
        return None

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


def extract_spotify_id_from_url(url: str) -> str | None:
    """Extract Spotify track ID from a Spotify URL."""
    match = re.search(r"spotify\.com/track/([a-zA-Z0-9]+)", url)
    return match.group(1) if match else None


def _extract_track_info(track: dict) -> dict:
    """Extract standardized track info from Spotify track object."""
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    album = track.get("album", {})
    duration_ms = track.get("duration_ms", 0)
    minutes = duration_ms // 60000
    seconds = (duration_ms % 60000) // 1000

    copyrights = album.get("copyrights", [])
    label = album.get("label", "")

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
        "label": label,
        "copyrights": copyrights,
        "popularity": track.get("popularity", 0),
        "source": "Spotify",
    }
