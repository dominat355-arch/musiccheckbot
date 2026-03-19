"""Lyrics retrieval — Genius (primary) + lyrics.ovh (fallback)."""

import logging
import urllib.parse

import requests

from config import GENIUS_ACCESS_TOKEN

logger = logging.getLogger(__name__)

API_TIMEOUT = 20

# ── Genius API ───────────────────────────────────────────


def fetch_genius(title: str, artist: str) -> dict | None:
    """Fetch lyrics from Genius API."""
    if not GENIUS_ACCESS_TOKEN:
        logger.warning("Genius token not configured, skipping.")
        return None

    try:
        # Search for the song
        headers = {"Authorization": f"Bearer {GENIUS_ACCESS_TOKEN}"}
        query = f"{artist} {title}"
        resp = requests.get(
            "https://api.genius.com/search",
            params={"q": query},
            headers=headers,
            timeout=API_TIMEOUT,
        )
        data = resp.json()
        hits = data.get("response", {}).get("hits", [])

        if not hits:
            return None

        # Get the first song result
        song_hit = None
        for hit in hits:
            if hit.get("type") == "song":
                song_hit = hit
                break
        if not song_hit:
            song_hit = hits[0]

        song_info = song_hit.get("result", {})
        song_url = song_info.get("url", "")
        song_id = song_info.get("id")

        if not song_id:
            return None

        # Fetch full lyrics via scraping the Genius page
        # (Genius API doesn't return lyrics directly, only metadata)
        lyrics_text = _scrape_genius_lyrics(song_url)

        return {
            "lyrics": lyrics_text,
            "url": song_url,
            "genius_title": song_info.get("full_title", ""),
            "source": "Genius",
        }
    except Exception as e:
        logger.error("Genius error: %s", e)
        return None


def _scrape_genius_lyrics(url: str) -> str | None:
    """Scrape lyrics text from a Genius song page."""
    try:
        resp = requests.get(url, timeout=API_TIMEOUT)
        if resp.status_code != 200:
            return None

        from html.parser import HTMLParser

        class LyricsParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_lyrics = False
                self.lyrics_parts = []
                self.depth = 0

            def handle_starttag(self, tag, attrs):
                attr_dict = dict(attrs)
                data_attr = attr_dict.get("data-lyrics-container")
                if data_attr == "true":
                    self.in_lyrics = True
                    self.depth = 0
                if self.in_lyrics:
                    self.depth += 1
                    if tag == "br":
                        self.lyrics_parts.append("\n")

            def handle_endtag(self, tag):
                if self.in_lyrics:
                    self.depth -= 1
                    if self.depth <= 0:
                        self.in_lyrics = False

            def handle_data(self, data):
                if self.in_lyrics:
                    self.lyrics_parts.append(data)

        parser = LyricsParser()
        parser.feed(resp.text)
        lyrics = "".join(parser.lyrics_parts).strip()
        return lyrics if lyrics else None
    except Exception as e:
        logger.error("Genius scrape error: %s", e)
        return None


# ── lyrics.ovh (fallback) ────────────────────────────────


def fetch_lyrics_ovh(title: str, artist: str) -> dict | None:
    """Fallback lyrics from lyrics.ovh (no API key needed)."""
    try:
        artist_enc = urllib.parse.quote(artist)
        title_enc = urllib.parse.quote(title)
        resp = requests.get(
            f"https://api.lyrics.ovh/v1/{artist_enc}/{title_enc}",
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            lyrics = data.get("lyrics", "").strip()
            if lyrics:
                return {
                    "lyrics": lyrics,
                    "url": None,
                    "source": "lyrics.ovh",
                }
        return None
    except Exception as e:
        logger.error("lyrics.ovh error: %s", e)
        return None


# ── Main function ────────────────────────────────────────


def fetch_lyrics(title: str, artist: str) -> dict:
    """Try all lyrics sources in order. Always returns a dict."""
    result = fetch_genius(title, artist)
    if result and result.get("lyrics"):
        return result

    result = fetch_lyrics_ovh(title, artist)
    if result and result.get("lyrics"):
        return result

    # No lyrics found
    search_q = urllib.parse.quote(f"{artist} {title}")
    return {
        "lyrics": None,
        "url": f"https://genius.com/search?q={search_q}",
        "source": "none",
    }
