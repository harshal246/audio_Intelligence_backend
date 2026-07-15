# Audio preprocessing service - handles audio chunking and preparation
import os
from pathlib import Path
from typing import List, Tuple

from pydub import AudioSegment

from app.config import settings


def get_audio_duration_minutes(audio_path: str) -> float:
    """Return the duration of an audio file in minutes."""
    audio = AudioSegment.from_file(audio_path)
    return len(audio) / 60000.0  # ms → minutes


def chunk_audio(audio_path: str, chunk_minutes: int = 15) -> List[Tuple[str, float]]:
    """
    Chunk audio file into smaller segments for processing.
    
    Args:
        audio_path: Path to the audio file (any format supported by ffmpeg)
        chunk_minutes: Duration of each chunk in minutes (default: 15)
    
    Returns:
        List of tuples (chunk_path, offset_seconds)
    """
    audio = AudioSegment.from_file(audio_path)
    chunk_ms = chunk_minutes * 60 * 1000
    chunks = []
    
    # Create chunk directory if it doesn't exist
    chunk_dir = Path(settings.CHUNK_DIR)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    # Determine output format from source file extension
    ext = Path(audio_path).suffix.lower().lstrip(".")
    out_fmt = ext if ext in ("wav", "mp3", "flac", "ogg", "m4a", "aac") else "wav"
    
    for i, start in enumerate(range(0, len(audio), chunk_ms)):
        chunk = audio[start:start + chunk_ms]
        chunk_path = chunk_dir / f"chunk_{i:03d}.{out_fmt}"
        chunk.export(str(chunk_path), format=out_fmt)
        chunks.append((str(chunk_path), start / 1000.0))  # (path, offset_seconds)
    
    return chunks


def cleanup_chunks():
    """Remove all files from the chunks directory."""
    chunk_dir = Path(settings.CHUNK_DIR)
    if chunk_dir.exists():
        for file in chunk_dir.iterdir():
            if file.is_file():
                file.unlink()

