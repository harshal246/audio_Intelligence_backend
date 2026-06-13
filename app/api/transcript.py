# Transcript API endpoints - handles audio upload and transcription
import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status, BackgroundTasks
from sqlalchemy.orm import Session

from app.config import settings
from app.database.db import get_db, SessionLocal
from app.models.user import User
from app.services.transcript_service import process_audio_pipeline, format_transcript_output
from app.utils.auth import get_current_user

router = APIRouter(prefix="/transcribe", tags=["transcript"])


def background_audio_processing(audio_path: str, user_id, audio_filename: str):
    """Wrapper to run the audio pipeline in the background with a dedicated DB session."""
    db = SessionLocal()
    try:
        process_audio_pipeline(audio_path, user_id, db, audio_filename)
    except Exception as e:
        print(f"Background audio processing failed: {str(e)}")
    finally:
        db.close()


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def transcribe_audio(
    audio: UploadFile,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload and transcribe audio file.
    
    This endpoint:
    1. Validates JWT token
    2. Validates audio format
    3. Saves audio file locally
    4. Processes audio through WhisperX + diarization pipeline
    5. Stores transcript in database
    6. Returns diarized transcript
    
    Args:
        audio: WAV audio file upload
        current_user: Authenticated user (from JWT)
        db: Database session
    
    Returns:
        Dictionary with transcript segments and formatted output
    """
    # Accept any audio format — validate it's not empty or a non-audio file by extension
    SUPPORTED_FORMATS = ('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.aac', '.wma', '.webm', '.mp4')
    file_ext = Path(audio.filename).suffix.lower()
    if file_ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{file_ext}'. Supported formats: {', '.join(SUPPORTED_FORMATS)}"
        )
    
    # Validate file size (max 1GB)
    MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1GB
    content = await audio.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 1GB limit"
        )
    
    # Create upload directory if it doesn't exist
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Save original uploaded file
    original_path = upload_dir / audio.filename
    with open(original_path, "wb") as f:
        f.write(content)
    
    # Convert to WAV if not already WAV
    file_ext = Path(audio.filename).suffix.lower()
    if file_ext != '.wav':
        try:
            from pydub import AudioSegment
            audio_segment = AudioSegment.from_file(str(original_path))
            wav_filename = Path(audio.filename).stem + ".wav"
            wav_path = upload_dir / wav_filename
            audio_segment.export(str(wav_path), format="wav")
            os.remove(str(original_path))  # clean up original
            final_path = str(wav_path)
            print(f"Converted '{audio.filename}' -> '{wav_filename}'")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not convert audio file to WAV: {str(e)}"
            )
    else:
        final_path = str(original_path)
    
    # Queue the background task with the WAV file path
    background_tasks.add_task(
        background_audio_processing,
        final_path,
        current_user.id,
        audio.filename
    )
    
    return {
        "status": "processing",
        "message": "Audio uploaded successfully. Transcription is running in the background.",
        "audio_filename": audio.filename
    }


@router.get("/", status_code=status.HTTP_200_OK)
async def get_transcripts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all transcripts for the current user.
    
    Args:
        current_user: Authenticated user (from JWT)
        db: Database session
    
    Returns:
        List of transcript segments
    """
    from app.models.transcript import Transcript
    from sqlalchemy import select
    
    transcripts = db.execute(
        select(Transcript).where(Transcript.user_id == current_user.id)
    ).scalars().all()
    
    return {
        "status": "success",
        "transcripts": [
            {
                "id": str(t.id),
                "audio_filename": t.audio_filename,
                "full_transcript_data": t.full_transcript_data,
                "processing_timestamp": t.processing_timestamp.isoformat()
            }
            for t in transcripts
        ]
    }
