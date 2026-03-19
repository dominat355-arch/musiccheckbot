"""Audio fingerprinting — ACRCloud (primary) + AudD (fallback) + Shazam (fallback 2)."""

import asyncio
import base64
import concurrent.futures
import hashlib
import hmac
import logging
import time
import threading

import requests

from config import (
    ACRCLOUD_ACCESS_KEY,
    ACRCLOUD_ACCESS_SECRET,
    ACRCLOUD_HOST,
    AUDD_API_TOKEN,
)

logger = logging.getLogger(__name__)

API_TIMEOUT = 15


def recognize_acrcloud(audio_path: str) -> dict | None:
    """Identify a track using ACRCloud audio fingerprinting."""
    if not ACRCLOUD_ACCESS_KEY or not ACRCLOUD_ACCESS_SECRET:
        logger.warning("ACRCloud credentials not configured, skipping.")
        return None

    try:
        timestamp = str(int(time.time()))
        string_to_sign = (
            f"POST\n/v1/identify\n{ACRCLOUD_ACCESS_KEY}\naudio\n1\n{timestamp}"
        )
        sign = base64.b64encode(
            hmac.new(
                ACRCLOUD_ACCESS_SECRET.encode("ascii"),
                string_to_sign.encode("ascii"),
                digestmod=hashlib.sha1,
            ).digest()
        ).decode("ascii")

        with open(audio_path, "rb") as f:
            sample = f.read(1024 * 1024)  # Send max 1MB sample

        data = {
            "access_key": ACRCLOUD_ACCESS_KEY,
            "sample_bytes": str(len(sample)),
            "timestamp": timestamp,
            "signature": sign,
            "data_type": "audio",
            "signature_version": "1",
        }
        files = {"sample": ("sample.wav", sample, "audio/wav")}

        resp = requests.post(
            f"https://{ACRCLOUD_HOST}/v1/identify",
            data=data,
            files=files,
            timeout=API_TIMEOUT,
        )
        result = resp.json()

        if result.get("status", {}).get("code") == 0:
            music = result["metadata"]["music"][0]
            return {
                "title": music.get("title", "Unknown"),
                "artist": ", ".join(
                    a["name"] for a in music.get("artists", [{"name": "Unknown"}])
                ),
                "album": music.get("album", {}).get("name", "Unknown"),
                "release_date": music.get("release_date", ""),
                "isrc": music.get("external_ids", {}).get("isrc", ""),
                "spotify_id": (
                    music.get("external_metadata", {})
                    .get("spotify", {})
                    .get("track", {})
                    .get("id", "")
                ),
                "source": "ACRCloud",
            }
        else:
            logger.info(
                "ACRCloud no match: %s", result.get("status", {}).get("msg", "")
            )
            return None
    except Exception as e:
        logger.error("ACRCloud error: %s", e)
        return None


def recognize_audd(audio_path: str) -> dict | None:
    """Fallback identification using AudD."""
    if not AUDD_API_TOKEN:
        logger.warning("AudD token not configured, skipping.")
        return None

    try:
        with open(audio_path, "rb") as f:
            data = {"api_token": AUDD_API_TOKEN, "return": "spotify"}
            files = {"file": f}
            resp = requests.post(
                "https://api.audd.io/", data=data, files=files, timeout=API_TIMEOUT
            )
            result = resp.json()

        if result.get("status") == "success" and result.get("result"):
            r = result["result"]
            spotify_id = ""
            if r.get("spotify") and r["spotify"].get("id"):
                spotify_id = r["spotify"]["id"]
            return {
                "title": r.get("title", "Unknown"),
                "artist": r.get("artist", "Unknown"),
                "album": r.get("album", "Unknown"),
                "release_date": r.get("release_date", ""),
                "isrc": "",
                "spotify_id": spotify_id,
                "source": "AudD",
            }
        else:
            logger.info("AudD no match.")
            return None
    except Exception as e:
        logger.error("AudD error: %s", e)
        return None


def _run_shazam_in_thread(audio_path: str) -> dict | None:
    """Run Shazam recognition in a completely separate thread with its own event loop."""
    result_container = {"result": None}

    def _worker():
        try:
            from shazamio import Shazam
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                shazam = Shazam()
                result = loop.run_until_complete(shazam.recognize(audio_path))
                if result and result.get("track"):
                    track = result["track"]
                    spotify_id = ""
                    for provider in track.get("hub", {}).get("providers", []):
                        if provider.get("type") == "SPOTIFY":
                            for action in provider.get("actions", []):
                                uri = action.get("uri", "")
                                if "spotify:track:" in uri:
                                    spotify_id = uri.split("spotify:track:")[-1]

                    result_container["result"] = {
                        "title": track.get("title", "Unknown"),
                        "artist": track.get("subtitle", "Unknown"),
                        "album": "",
                        "release_date": "",
                        "isrc": track.get("isrc", ""),
                        "spotify_id": spotify_id,
                        "source": "Shazam",
                    }
            finally:
                loop.close()
        except Exception as e:
            logger.error("Shazam worker error: %s", e)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=25)

    return result_container["result"]


def recognize_shazam(audio_path: str) -> dict | None:
    """Fallback identification using Shazam (shazamio) — runs in isolated thread."""
    try:
        return _run_shazam_in_thread(audio_path)
    except Exception as e:
        logger.error("Shazam error: %s", e)
        return None


def recognize_audio(audio_path: str) -> dict | None:
    """Try all recognition tiers in order."""
    result = recognize_acrcloud(audio_path)
    if result:
        return result

    result = recognize_audd(audio_path)
    if result:
        return result

    result = recognize_shazam(audio_path)
    if result:
        return result

    return None
