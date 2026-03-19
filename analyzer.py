"""Core music analysis logic — orchestrates all modules + wedding suitability."""

import logging
import re

from audio_utils import analyze_offline, cleanup_temp_files, convert_to_wav
from lyrics_client import fetch_lyrics
from recognizer import recognize_audio
from spotify_client import (
    extract_spotify_id_from_url,
    get_audio_features,
    get_track_by_id,
    search_track,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# THEMATIC CONTENT FLAGS (wedding-inappropriate themes)
# ═══════════════════════════════════════════════════════════

THEMATIC_FLAGS = {
    "breakup": [
        "break up", "breaking up", "broke up", "we're done", "we are done",
        "goodbye", "let you go", "moving on", "it's over", "it is over",
        "left me", "left you", "walk away", "walked away", "leaving you",
        "goodbye forever", "last time", "never coming back", "fall apart",
        "falling apart", "torn apart", "broken heart", "heartbreak",
        "heartbroken", "heart is breaking", "crying over you", "without you",
        "can't be together", "not meant to be", "never meant to be",
        "should've let go", "too late for us", "too late now",
        "used to love", "used to be", "what we had",
    ],
    "infidelity": [
        "cheating", "cheat on", "cheated on", "another woman", "another man",
        "another girl", "another guy", "side piece", "side chick",
        "affair", "behind my back", "behind your back", "sneak around",
        "sneaking around", "lied to me", "lied to you", "two-timing",
        "playing games", "played me", "played you",
        "you were with her", "you were with him", "saw you with",
        "sleeping with", "sleeping around", "other woman", "other man",
        "unfaithful", "betray", "betrayed", "betrayal",
    ],
    "violence": [
        "blood", "bleeding", "bleed", "kill", "killed", "killing",
        "murder", "murdered", "shoot", "shot", "gun", "knife",
        "stabbed", "stab", "cut me", "cut you", "wound", "wounded",
        "punch", "fighting", "beat you", "beat me", "weapon", "destroy",
        "burn it down", "set fire", "revenge", "pull the trigger",
    ],
    "death": [
        "die", "died", "dying", "death", "dead", "funeral",
        "grave", "graveyard", "cemetery", "bury", "buried",
        "suicide", "end my life", "kill myself", "want to die",
        "better off dead", "six feet under", "gone forever",
        "never wake up", "last breath", "rest in peace",
    ],
    "criminal": [
        "cocaine", "heroin", "smoke weed",
        "getting high", "get high", "getting wasted", "wasted",
        "prison", "jail", "locked up", "handcuffs", "arrest",
        "run from the cops", "on the run", "stolen", "stealing",
        "robbery", "dealer", "dealing", "criminal",
    ],
    "toxic": [
        "hate you", "i hate you", "hate myself",
        "i despise", "despise you", "you disgust me",
        "worthless", "you're worthless", "pathetic", "you're nothing",
        "meant nothing", "you mean nothing", "toxic", "poison",
        "manipulate", "manipulating", "controlling",
        "abuse", "abusive", "scream at",
        "i don't love you", "never loved you", "never loved me",
        "fell out of love", "stop loving",
    ],
    "dark_emotion": [
        "so alone", "all alone", "completely alone", "no one cares",
        "nobody cares", "nobody loves", "no one loves",
        "can't go on", "can't keep going", "give up",
        "given up", "nothing left", "nothing matters",
        "empty inside", "feel nothing", "numb", "hopeless",
        "lost everything", "hit rock bottom", "at rock bottom",
        "falling apart", "falling down", "sinking", "drowning in",
    ],
}

SEVERITY = {
    "violence": "🔴 HIGH",
    "death": "🔴 HIGH",
    "infidelity": "🔴 HIGH",
    "criminal": "🟠 MEDIUM",
    "breakup": "🟠 MEDIUM",
    "toxic": "🟠 MEDIUM",
    "dark_emotion": "🟡 LOW",
}

LABELS = {
    "violence": "Violence / Physical harm",
    "death": "Death / Dark themes",
    "infidelity": "Infidelity / Cheating",
    "criminal": "Crime / Drugs",
    "breakup": "Breakup / Heartbreak",
    "toxic": "Toxic relationship / Hate",
    "dark_emotion": "Loneliness / Depression",
}


def thematic_scan(lyrics_text: str) -> list:
    """Scan lyrics for wedding-inappropriate themes."""
    if not lyrics_text:
        return []

    findings = []
    lyrics_lower = lyrics_text.lower()

    for category, phrases in THEMATIC_FLAGS.items():
        matched = []
        for phrase in phrases:
            if phrase in lyrics_lower:
                for line in lyrics_text.split("\n"):
                    if phrase in line.lower() and line.strip():
                        matched.append((phrase, line.strip()[:80]))
                        break
                if len(matched) >= 2:
                    break

        if matched:
            findings.append(
                {
                    "category": LABELS[category],
                    "severity": SEVERITY[category],
                    "examples": matched[:2],
                }
            )

    return findings


# ═══════════════════════════════════════════════════════════
# WEDDING SUITABILITY LOGIC
# ═══════════════════════════════════════════════════════════


def wedding_suitability(
    features: dict, is_explicit: bool, lyrics_text: str | None
) -> dict:
    """Determine if track is suitable for wedding video use."""
    issues = []
    placement = []
    immediate_disqualifiers = []

    # Hard disqualifiers
    if is_explicit:
        immediate_disqualifiers.append("Explicit content (Spotify flagged)")

    thematic_issues = thematic_scan(lyrics_text) if lyrics_text else []
    high_severity = [t for t in thematic_issues if "HIGH" in t["severity"]]
    medium_severity = [t for t in thematic_issues if "MEDIUM" in t["severity"]]
    low_severity = [t for t in thematic_issues if "LOW" in t["severity"]]

    if high_severity:
        for item in high_severity:
            immediate_disqualifiers.append(item["category"])

    if immediate_disqualifiers:
        return {
            "verdict": "❌ Not recommended for wedding video",
            "issues": immediate_disqualifiers,
            "thematic": thematic_issues,
            "placement": [],
        }

    # BPM-based placement
    bpm = features.get("tempo", 0)
    if bpm < 65:
        placement.append("ceremony walk / recessional")
    elif bpm < 85:
        placement.append("first dance / slow dance / romantic moments")
    elif bpm < 110:
        placement.append("getting ready / portraits / golden hour")
    elif bpm < 135:
        placement.append("reception highlights / couple moments")
    else:
        placement.append("party / dance floor / reception party")

    # Feature scoring
    score = 0
    energy = features.get("energy", 0.5)
    valence = features.get("valence", 0.5)
    instrumentalness = features.get("instrumentalness", 0)
    acousticness = features.get("acousticness", 0)

    if valence < 0.2:
        issues.append("Very melancholic mood — may not suit celebration")
        score -= 20
    if energy > 0.9:
        issues.append("Very high energy — best for party scenes only")
        score -= 10
    if instrumentalness > 0.7:
        score += 20
        placement.insert(0, "cinematic highlights / full film")
    if acousticness > 0.7:
        score += 15

    if medium_severity:
        for item in medium_severity:
            issues.append(f"Theme: {item['category']}")
        score -= 15 * len(medium_severity)

    if low_severity:
        for item in low_severity:
            issues.append(f"Note: {item['category']} — minor concern")

    # Final verdict
    if not issues and not thematic_issues:
        verdict = "✅ Safe for wedding video"
    elif not medium_severity and not high_severity:
        verdict = "✅ Generally safe — minor notes below"
    elif len(medium_severity) <= 1 and not high_severity:
        verdict = "⚠️ Use with caution — check thematic warnings"
    else:
        verdict = "❌ Not recommended — multiple content concerns"

    return {
        "verdict": verdict,
        "issues": issues,
        "thematic": thematic_issues,
        "placement": placement,
    }


# ═══════════════════════════════════════════════════════════
# COPYRIGHT RISK ASSESSMENT
# ═══════════════════════════════════════════════════════════

MAJOR_LABELS = {
    "universal", "umg", "sony", "warner", "atlantic", "columbia",
    "republic", "interscope", "def jam", "capitol", "island",
    "rca", "epic", "parlophone", "virgin", "emi", "polydor",
}


def copyright_risk(track_info: dict, features: dict | None) -> dict:
    """Assess copyright risk for video use."""
    label = track_info.get("label", "").lower()
    copyrights = track_info.get("copyrights", [])
    instrumentalness = features.get("instrumentalness", 0) if features else 0

    # Check label
    is_major = any(ml in label for ml in MAJOR_LABELS)
    has_copyright_notice = bool(copyrights)

    if instrumentalness > 0.85:
        risk = "🟢 Low"
        note = "Mostly instrumental — lower detection risk"
    elif is_major or has_copyright_notice:
        risk = "🔴 High"
        note = "Major label / distributed — YouTube/Instagram will likely flag"
    elif label:
        risk = "🟡 Medium"
        note = "Indie/small label — may still be flagged"
    else:
        risk = "🟡 Medium"
        note = "Label unknown — verify before publishing"

    return {
        "risk": risk,
        "note": note,
        "label": track_info.get("label", "Unknown"),
        "disclaimer": "Copyright assessment is informational only. Always verify with the platform's rights database before publishing.",
    }


# ═══════════════════════════════════════════════════════════
# EXPLICIT CONTENT DETECTION
# ═══════════════════════════════════════════════════════════


def check_explicit(is_spotify_explicit: bool, lyrics_text: str | None) -> dict:
    """Multi-layer explicit content detection."""
    profanity_detected = False

    if lyrics_text:
        try:
            from better_profanity import profanity

            profanity_detected = profanity.contains_profanity(lyrics_text)
        except ImportError:
            logger.warning("better-profanity not installed, skipping profanity check.")

    if is_spotify_explicit and profanity_detected:
        return {
            "label": "🔞 Explicit",
            "detail": "Flagged by Spotify + profanity detected in lyrics",
        }
    elif is_spotify_explicit:
        return {
            "label": "🔞 Explicit",
            "detail": "Flagged as explicit by Spotify",
        }
    elif profanity_detected:
        return {
            "label": "⚠️ Contains strong language (unrated)",
            "detail": "Profanity detected but not flagged by Spotify",
        }
    else:
        return {
            "label": "✅ Clean",
            "detail": "No explicit content detected",
        }


# ═══════════════════════════════════════════════════════════
# MAIN ANALYSIS ORCHESTRATOR
# ═══════════════════════════════════════════════════════════


def quick_identify_audio(audio_path: str) -> dict | None:
    """Quick identification only — returns basic track info fast."""
    try:
        track_info = recognize_audio(audio_path)
        if not track_info:
            return {"error": "not_identified"}
        return track_info
    except Exception as e:
        logger.error("Quick identify error: %s", e)
        return None


def analyze_from_audio(audio_path: str, preloaded_track: dict = None) -> dict:
    """Full analysis pipeline from an audio file."""
    wav_path = None
    try:
        # Step 1: Use preloaded track or recognize fresh
        track_info = preloaded_track if preloaded_track else recognize_audio(audio_path)

        if not track_info or track_info.get("error"):
            return {"error": "not_identified"}

        # Step 2: Enrich with Spotify data
        spotify_id = track_info.get("spotify_id", "")
        spotify_track = None
        features = None

        if spotify_id:
            spotify_track = get_track_by_id(spotify_id)
            features = get_audio_features(spotify_id)
        else:
            # Try searching Spotify by name
            query = f"{track_info['artist']} {track_info['title']}"
            spotify_track = search_track(query)
            if spotify_track and spotify_track.get("spotify_id"):
                features = get_audio_features(spotify_track["spotify_id"])

        # Merge track info
        if spotify_track:
            track_info.update(
                {
                    k: v
                    for k, v in spotify_track.items()
                    if v and k not in ("source",)
                }
            )

        # Fallback: offline analysis
        if not features:
            wav_path = convert_to_wav(audio_path)
            if wav_path:
                features = analyze_offline(wav_path)

        return _build_result(track_info, features)
    finally:
        cleanup_temp_files(wav_path) if wav_path else None


def analyze_from_text(query: str) -> dict:
    """Full analysis pipeline from a text search query."""
    # Try Spotify search
    track_info = search_track(query)
    if not track_info:
        return {"error": "not_found", "query": query}

    features = None
    if track_info.get("spotify_id"):
        features = get_audio_features(track_info["spotify_id"])

    return _build_result(track_info, features)


def analyze_from_url(url: str) -> dict:
    """Analyze from a Spotify or YouTube URL."""
    # Spotify URL
    spotify_id = extract_spotify_id_from_url(url)
    if spotify_id:
        track_info = get_track_by_id(spotify_id)
        if not track_info:
            return {"error": "not_found", "query": url}
        features = get_audio_features(spotify_id)
        return _build_result(track_info, features)

    # YouTube URL — extract title and search Spotify
    yt_match = re.search(
        r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)", url
    )
    if yt_match:
        return {
            "error": "youtube_not_supported",
            "note": "YouTube link detection coming soon. For now, type the artist and song name instead.",
        }

    return {"error": "unsupported_url", "url": url}


def _build_result(track_info: dict, features: dict | None) -> dict:
    """Assemble the final analysis result."""
    title = track_info.get("title", "Unknown")
    artist = track_info.get("artist", "Unknown")

    # Lyrics
    lyrics_data = fetch_lyrics(title, artist)
    lyrics_text = lyrics_data.get("lyrics")

    # Explicit check
    is_explicit = track_info.get("is_explicit", False)
    explicit = check_explicit(is_explicit, lyrics_text)

    # Wedding suitability
    if features:
        wedding = wedding_suitability(features, is_explicit, lyrics_text)
    else:
        wedding = {
            "verdict": "⚠️ Partial analysis — audio features unavailable",
            "issues": ["Could not retrieve audio features from Spotify or offline analysis"],
            "thematic": thematic_scan(lyrics_text) if lyrics_text else [],
            "placement": [],
        }

    # Copyright risk
    cr = copyright_risk(track_info, features)

    return {
        "track": track_info,
        "features": features,
        "lyrics": lyrics_data,
        "explicit": explicit,
        "wedding": wedding,
        "copyright": cr,
    }
