# Speaker diarization service - handles speaker identification using PyAnnote
import time
import huggingface_hub
import pandas as pd
import torch
import whisperx
from whisperx.diarize import DiarizationPipeline

from app.config import settings
from app.services.preprocessing_service import chunk_audio


def get_device_config():
    """Detect GPU and return device configuration."""
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def diarize_audio(audio_path: str, min_speakers: int = 2, max_speakers: int = 4):
    """
    Perform speaker diarization on audio file.
    
    Args:
        audio_path: Path to the audio file
        min_speakers: Minimum number of speakers to detect
        max_speakers: Maximum number of speakers to detect
    
    Returns:
        DataFrame with diarization segments
    """
    device = get_device_config()
    
    print("Running speaker diarization...")
    diar_start = time.time()
    
    # Authenticate with Hugging Face
    huggingface_hub.login(token=settings.HF_TOKEN)
    
    # Load diarization pipeline
    diarize_model = DiarizationPipeline(
        model_name="pyannote/speaker-diarization-3.1",
        device=device
    )
    
    # Chunk audio into 20-minute segments
    print("Chunking audio into 20-min segments...")
    chunks = chunk_audio(audio_path, chunk_minutes=20)
    print(f"{len(chunks)} chunk(s) created")
    
    # Diarize each chunk with offset timestamps
    all_diarized = []
    for chunk_path, offset in chunks:
        print(f"Diarizing {chunk_path} (offset: {offset:.0f}s)...")
        seg_df = diarize_model(chunk_path, min_speakers=min_speakers, max_speakers=max_speakers)
        seg_df["start"] += offset
        seg_df["end"] += offset
        all_diarized.append(seg_df)
    
    diarize_segments = pd.concat(all_diarized).reset_index(drop=True)
    
    diar_time = time.time() - diar_start
    print(f"Diarization complete — {diar_time:.1f}s")
    
    return diarize_segments


def assign_speakers_to_segments(diarize_segments: pd.DataFrame, result: dict):
    """
    Assign speaker labels to transcript segments.
    
    Args:
        diarize_segments: DataFrame with diarization results
        result: Transcription result with segments
    
    Returns:
        Transcription result with speaker labels assigned
    """
    print("Merging diarization with transcript...")
    
    # Check if we have word-level timestamps (WhisperX)
    has_words = False
    if result.get("segments") and len(result["segments"]) > 0:
        has_words = "words" in result["segments"][0]
        
    if has_words:
        result = whisperx.assign_word_speakers(diarize_segments, result)
    else:
        # Custom segment-level overlap assignment for outputs without word timestamps (like Gemini)
        for segment in result.get("segments", []):
            seg_start = segment.get("start", 0)
            seg_end = segment.get("end", 0)
            
            best_speaker = "UNKNOWN"
            max_overlap = 0
            
            for _, diar_row in diarize_segments.iterrows():
                diar_start = diar_row["start"]
                diar_end = diar_row["end"]
                
                overlap_start = max(seg_start, diar_start)
                overlap_end = min(seg_end, diar_end)
                overlap = max(0, overlap_end - overlap_start)
                
                if overlap > max_overlap:
                    max_overlap = overlap
                    best_speaker = diar_row["speaker"]
            
            segment["speaker"] = best_speaker
            
    # Extract unique speakers
    speakers = sorted({
        seg.get("speaker", "UNKNOWN")
        for seg in result.get("segments", [])
        if seg.get("speaker")
    })
    print(f"Speakers detected: {len(speakers)}")
    
    return result
