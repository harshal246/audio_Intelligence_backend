# Audio preprocessing service - handles audio chunking and preparation
import os
from pathlib import Path
from typing import List, Tuple

from pydub import AudioSegment

from app.config import settings


def chunk_audio(audio_path: str, chunk_minutes: int = 20) -> List[Tuple[str, float]]:
    """
    Chunk audio file into smaller segments for processing.
    
    Args:
        audio_path: Path to the audio file
        chunk_minutes: Duration of each chunk in minutes (default: 20)
    
    Returns:
        List of tuples (chunk_path, offset_seconds)
    """
    audio = AudioSegment.from_wav(audio_path)
    chunk_ms = chunk_minutes * 60 * 1000
    chunks = []
    
    # Create chunk directory if it doesn't exist
    chunk_dir = Path(settings.CHUNK_DIR)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    
    for i, start in enumerate(range(0, len(audio), chunk_ms)):
        chunk = audio[start:start + chunk_ms]
        chunk_path = chunk_dir / f"chunk_{i:03d}.wav"
        chunk.export(str(chunk_path), format="wav")
        chunks.append((str(chunk_path), start / 1000.0))  # (path, offset_seconds)
    
    return chunks


def cleanup_chunks():
    """Remove all files from the chunks directory."""
    chunk_dir = Path(settings.CHUNK_DIR)
    if chunk_dir.exists():
        for file in chunk_dir.glob("*.wav"):
            file.unlink()
