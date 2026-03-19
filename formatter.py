"""Format analysis results into Telegram-friendly messages."""


def format_bpm_label(bpm: float) -> str:
    if bpm < 70:
        return "slow"
    elif bpm < 100:
        return "medium"
    elif bpm < 130:
        return "fast"
    else:
        return "very fast"


def format_energy_label(energy: float) -> str:
    if energy < 0.25:
        return "calm"
    elif energy < 0.5:
        return "moderate"
    elif energy < 0.75:
        return "energetic"
    else:
        return "intense"


def format_valence_label(valence: float) -> str:
    if valence < 0.15:
        return "sad"
    elif valence < 0.35:
        return "melancholic"
    elif valence < 0.55:
        return "neutral"
    elif valence < 0.75:
        return "positive"
    else:
        return "euphoric"


def format_pct(value: float) -> str:
    return f"{int(value * 100)}%"


def format_quick_id(track_info: dict) -> str:
    """Format a quick identification result — fast first response."""
    title = track_info.get("title", "Unknown")
    artist = track_info.get("artist", "Unknown")
    album = track_info.get("album", "")
    release = track_info.get("release_date", "")
    source = track_info.get("source", "")

    lines = ["✅ Track identified!"]
    lines.append(f"🎵 {artist} — {title}")
    if album and album != "Unknown":
        album_line = f"💿 {album}"
        if release:
            album_line += f" ({release[:4]})"
        lines.append(album_line)
    elif release:
        lines.append(f"📅 {release[:4]}")
    if source:
        lines.append(f"🔎 via {source}")
    lines.append("")
    lines.append("⏳ Running full analysis...")

    return "\n".join(lines)


def format_analysis(result: dict) -> str:
    """Format a full analysis result into a Telegram message."""

    # ── Error cases ──────────────────────────────────
    if result.get("error") == "not_identified":
        return (
            "🔍 Couldn't identify this track.\n\n"
            "Try typing the artist and song name instead:\n"
            "/analyze Artist - Song Title"
        )
    if result.get("error") == "not_found":
        q = result.get("query", "")
        return (
            f"🔍 No results found for: {q}\n\n"
            "Try a different spelling or add the artist name:\n"
            "/analyze Artist - Song Title"
        )
    if result.get("error") == "youtube_not_supported":
        return (
            "🔗 YouTube link support coming soon!\n\n"
            "For now, type the artist and song name:\n"
            "/analyze Artist - Song Title"
        )
    if result.get("error") == "unsupported_url":
        return (
            "🔗 This link type isn't supported yet.\n\n"
            "Send an audio file or type:\n"
            "/analyze Artist - Song Title"
        )
    if result.get("error"):
        return f"⚠️ Something went wrong: {result['error']}"

    # ── Track info ───────────────────────────────────
    track = result.get("track", {})
    features = result.get("features")
    lyrics_data = result.get("lyrics", {})
    explicit = result.get("explicit", {})
    wedding = result.get("wedding", {})
    cr = result.get("copyright", {})

    lines = []

    # Track identified
    lines.append("🎵 TRACK IDENTIFIED")
    lines.append(f"Artist: {track.get('artist', 'Unknown')}")
    lines.append(f"Title: {track.get('title', 'Unknown')}")
    album = track.get("album", "")
    release = track.get("release_date", "")
    if album:
        album_line = f"Album: {album}"
        if release:
            year = release[:4]
            album_line += f" ({year})"
        lines.append(album_line)
    duration = track.get("duration", "")
    if duration:
        lines.append(f"Duration: {duration}")

    lines.append("")

    # Audio analysis
    if features:
        lines.append("📊 AUDIO ANALYSIS")
        bpm = features.get("tempo", 0)
        lines.append(f"BPM: {bpm} ({format_bpm_label(bpm)})")
        key = features.get("key", "?")
        mode = features.get("mode", "")
        lines.append(f"Key: {key} {mode}")

        energy = features.get("energy", 0)
        lines.append(f"Energy: {format_pct(energy)} ({format_energy_label(energy)})")
        lines.append(f"Danceability: {format_pct(features.get('danceability', 0))}")

        valence = features.get("valence", 0)
        lines.append(
            f"Valence (mood): {format_pct(valence)} ({format_valence_label(valence)})"
        )
        lines.append(f"Acousticness: {format_pct(features.get('acousticness', 0))}")
        lines.append(
            f"Instrumentalness: {format_pct(features.get('instrumentalness', 0))}"
        )

        source_note = features.get("source", "")
        if source_note and source_note != "Spotify":
            lines.append(f"ℹ️ Source: {source_note}")
        lines.append("")
    else:
        lines.append("📊 AUDIO ANALYSIS")
        lines.append("⚠️ Audio features unavailable")
        lines.append("")

    # Wedding verdict
    lines.append("🎬 VIDEO EDITING VERDICT")
    lines.append(f"Wedding use: {wedding.get('verdict', 'Unknown')}")
    lines.append(f"Explicit content: {explicit.get('label', 'Unknown')}")
    lines.append(f"Copyright risk: {cr.get('risk', 'Unknown')}")

    placement = wedding.get("placement", [])
    if placement:
        lines.append(f"Best for: {' / '.join(placement)}")
    lines.append("")

    # Thematic warnings
    thematic = wedding.get("thematic", [])
    if thematic:
        lines.append("🚫 THEMATIC WARNINGS")
        for item in thematic:
            severity = item["severity"]
            category = item["category"]
            examples = item.get("examples", [])
            if examples:
                _, context_line = examples[0]
                lines.append(f'{severity} {category} — "{context_line}"')
            else:
                lines.append(f"{severity} {category}")
        lines.append("")
    else:
        lines.append("✅ Themes: All clear — appropriate for wedding use")
        lines.append("")

    # Lyrics preview
    lyrics_text = lyrics_data.get("lyrics")
    lyrics_url = lyrics_data.get("url")
    if lyrics_text:
        preview_lines = [l for l in lyrics_text.split("\n") if l.strip()][:6]
        preview = "\n".join(preview_lines)
        lines.append("📝 LYRICS PREVIEW")
        lines.append(preview)
        if lyrics_url:
            lines.append(f"\n🔗 Full lyrics: {lyrics_url}")
        lines.append("")
    else:
        if lyrics_url:
            lines.append(f"📝 Lyrics not found — search: {lyrics_url}")
        else:
            lines.append("📝 Lyrics: Not available")
        lines.append("")

    # Notes
    issues = wedding.get("issues", [])
    copyright_note = cr.get("note", "")
    disclaimer = cr.get("disclaimer", "")

    notes = []
    if issues:
        for issue in issues:
            if not issue.startswith("Theme:") and not issue.startswith("Note:"):
                notes.append(issue)
    if copyright_note:
        notes.append(copyright_note)

    if notes:
        lines.append("⚠️ NOTES")
        for note in notes:
            lines.append(f"• {note}")
        lines.append("")

    if disclaimer:
        lines.append(f"📋 {disclaimer}")

    return "\n".join(lines)


def format_welcome() -> str:
    return (
        "🎵 Welcome to MusicCheckBot\n\n"
        "Send me any audio file and I'll analyze it instantly:\n\n"
        "📊 BPM & Musical Key\n"
        "⚡ Energy, Mood & Danceability\n"
        "📝 Lyrics (with Genius link)\n"
        "🔞 Explicit content detection\n"
        "🚫 Thematic warnings (breakup, violence, etc.)\n"
        "🎬 Wedding/event video suitability verdict\n"
        "⚖️ Copyright risk assessment\n\n"
        "How to use:\n"
        "• Send an MP3, M4A, WAV, or any audio file\n"
        "• Or type: /analyze Artist - Song Title\n"
        "• Or paste a Spotify link\n\n"
        "I check multiple music databases for accuracy.\n\n"
        "Questions? /help"
    )


def format_help() -> str:
    return (
        "📖 MusicCheckBot — Full Feature List\n\n"
        "📤 SEND AUDIO\n"
        "Upload any audio file (MP3, M4A, WAV, OGG, FLAC) — "
        "I'll identify the track and return a full analysis.\n\n"
        "🔍 SEARCH BY NAME\n"
        "/analyze Ed Sheeran Perfect\n"
        "/analyze Beyoncé - Halo\n\n"
        "🔗 PASTE A LINK\n"
        "Spotify track URLs work — just paste the link.\n\n"
        "📊 WHAT YOU GET\n"
        "• Track identification (artist, album, year)\n"
        "• BPM, musical key, energy, mood\n"
        "• Lyrics preview + Genius link\n"
        "• Explicit content flag\n"
        "• Thematic scan (breakup, violence, cheating, etc.)\n"
        "• Wedding video suitability verdict\n"
        "• Copyright risk assessment\n\n"
        "💡 TIPS\n"
        "• For best results, send at least 15 seconds of audio\n"
        "• If recognition fails, try typing the song name instead\n"
        "• The bot works with Telegram voice notes too\n\n"
        "Questions or feedback? /feedback"
    )


def format_about() -> str:
    return (
        "ℹ️ About MusicCheckBot\n\n"
        "Built for wedding videographers, content creators, and "
        "anyone who needs to know if a track is safe to use in their videos.\n\n"
        "Data sources:\n"
        "• ACRCloud & AudD — audio fingerprinting\n"
        "• Spotify — audio features & metadata\n"
        "• Genius — lyrics\n"
        "• librosa — offline audio analysis (fallback)\n\n"
        "The wedding suitability verdict considers:\n"
        "• Explicit content (Spotify flag + profanity scan)\n"
        "• Thematic content (breakup, violence, death, drugs, etc.)\n"
        "• Audio mood (energy, valence, BPM)\n"
        "• Copyright risk (label, distribution)\n\n"
        "All analysis is informational — always do your own due diligence."
    )
