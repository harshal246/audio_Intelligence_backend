# Transcript service - orchestrates the complete audio processing pipeline
import os
from pathlib import Path
from typing import List, Dict
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models.transcript import Transcript
from app.services.preprocessing_service import cleanup_chunks
from app.services.whisper_service import (
    transcribe_audio,
    align_transcription,
    cleanup_gpu_memory
)
from app.services.diarization_service import diarize_audio, assign_speakers_to_segments


def process_audio_pipeline(audio_path: str, user_id: UUID, db: Session, audio_filename: str) -> List[Dict]:
    """
    Complete audio processing pipeline:
    1. Transcribe audio with WhisperX
    2. Align transcript for improved timestamps
    3. Cleanup GPU memory
    4. Perform speaker diarization
    5. Assign speakers to transcript segments
    6. Store in database
    
    Args:
        audio_path: Path to the audio file
        user_id: UUID of the user who uploaded the audio
        db: Database session
        audio_filename: Original filename of the uploaded audio
    
    Returns:
        List of transcript segments with speaker information
    """
    # Step 1: Transcribe audio
    result = transcribe_audio(audio_path)
    
    # Step 2: Align transcript
    result = align_transcription(result, audio_path)
    
    # Store models for cleanup
    model = None  # Will be set by transcribe_audio if needed
    model_a = None  # Will be set by align_transcription if needed
    
    # Step 3: Cleanup GPU memory before diarization
    if model is not None or model_a is not None:
        cleanup_gpu_memory(model, model_a)
    
    # Step 4: Speaker diarization
    diarize_segments = diarize_audio(audio_path)
    
    # Step 5: Assign speakers to transcript segments
    result = assign_speakers_to_segments(diarize_segments, result)
    
    # Step 6: Format segments and store them as a single JSON object in database
    transcript_segments = []
    for seg in result["segments"]:
        text = seg["text"].strip()
        if not text:
            continue
        
        speaker = seg.get("speaker", "UNKNOWN")
        
        # Add to response array
        transcript_segments.append({
            "speaker": speaker,
            "start_time": seg["start"],
            "end_time": seg["end"],
            "text": text
        })
    
    # Create a single transcript record for the entire file
    transcript = Transcript(
        user_id=user_id,
        audio_filename=audio_filename,
        full_transcript_data=transcript_segments
    )
    db.add(transcript)
    db.commit()
    
    # Cleanup chunks
    cleanup_chunks()
    
    return transcript_segments


def format_transcript_output(segments: List[Dict]) -> str:
    """
    Format transcript segments into a readable string.
    
    Args:
        segments: List of transcript segment dictionaries
    
    Returns:
        Formatted transcript string
    """
    full_transcript = []
    current_speaker = None
    
    for seg in segments:
        speaker = seg["speaker"]
        
        if speaker != current_speaker:
            full_transcript.append(f"\n[{speaker}]")
            current_speaker = speaker
        
        line = f"[{seg['start_time']:7.2f}s → {seg['end_time']:7.2f}s] {seg['text']}"
        full_transcript.append(line)
    
    return "\n".join(full_transcript)
