"""Audio processing utilities — format conversion + offline BPM/key analysis."""

import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def convert_to_wav(input_path: str) -> str | None:
    """Convert any audio format to WAV using pydub."""
    try:
        from pydub import AudioSegment

        ext = os.path.splitext(input_path)[1].lower().lstrip(".")
        format_map = {
            "mp3": "mp3",
            "m4a": "mp4",
            "wav": "wav",
            "ogg": "ogg",
            "oga": "ogg",
            "flac": "flac",
            "aac": "aac",
        }
        fmt = format_map.get(ext, ext)
        audio = AudioSegment.from_file(input_path, format=fmt)

        out_path = tempfile.mktemp(suffix=".wav")
        audio.export(out_path, format="wav")
        return out_path
    except Exception as e:
        logger.error("Audio conversion error: %s", e)
        return None


def get_audio_duration(audio_path: str) -> float:
    """Get duration in seconds."""
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(audio_path)
        return len(audio) / 1000.0
    except Exception as e:
        logger.error("Duration check error: %s", e)
        return 0.0


def analyze_offline(audio_path: str) -> dict | None:
    """Offline BPM and key analysis using librosa (fallback when Spotify unavailable)."""
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(audio_path, duration=60)  # Analyze first 60s

        # BPM
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        if hasattr(tempo, "__len__"):
            tempo = float(tempo[0])
        else:
            tempo = float(tempo)

        # Key estimation via chromagram
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        key_profile = chroma.mean(axis=1)
        key_idx = int(np.argmax(key_profile))
        key_name = key_names[key_idx]

        # Simple major/minor detection
        # Major profile peaks: root, major 3rd (4 semitones), 5th (7 semitones)
        major_score = (
            key_profile[key_idx]
            + key_profile[(key_idx + 4) % 12]
            + key_profile[(key_idx + 7) % 12]
        )
        minor_score = (
            key_profile[key_idx]
            + key_profile[(key_idx + 3) % 12]
            + key_profile[(key_idx + 7) % 12]
        )
        mode = "major" if major_score >= minor_score else "minor"

        # Energy (RMS)
        rms = librosa.feature.rms(y=y)
        energy = float(np.clip(rms.mean() * 5, 0, 1))  # Normalize roughly to 0-1

        # Spectral centroid as a rough "brightness" proxy
        spectral = librosa.feature.spectral_centroid(y=y, sr=sr)
        brightness = float(np.clip(spectral.mean() / 5000, 0, 1))

        return {
            "tempo": round(tempo, 1),
            "key": key_name,
            "mode": mode,
            "energy": round(energy, 3),
            "danceability": round((energy + brightness) / 2, 3),  # Rough estimate
            "valence": 0.5,  # Can't reliably estimate from audio alone
            "acousticness": 0.5,
            "instrumentalness": 0.5,
            "speechiness": 0.0,
            "loudness": 0.0,
            "time_signature": 4,
            "source": "librosa (offline)",
        }
    except ImportError:
        logger.warning("librosa not available for offline analysis.")
        return None
    except Exception as e:
        logger.error("Offline analysis error: %s", e)
        return None


def cleanup_temp_files(*paths: str):
    """Remove temporary audio files."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
