# Transcript API endpoints - handles audio upload and transcription
import os
from pathlib import Path
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File, status, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import settings
from app.database.db import get_db, SessionLocal
from app.models.user import User
from app.services.transcript_service import process_audio_pipeline, format_transcript_output, save_simple_transcript, transcribe_simple_audio
from app.services.summary_service import generate_preview_summary
from app.utils.auth import get_current_user

router = APIRouter(prefix="/transcribe", tags=["transcript"])


@router.post("/simple", status_code=status.HTTP_201_CREATED)
async def transcribe_simple(
    audio: Optional[UploadFile] = File(default=None),
    transcript_text: Optional[str] = Form(default=None),
    audio_filename: Optional[str] = Form(default=None),
    generate_summary: bool = Query(default=False, description="If true, generate a preview summary after saving and return it in the same response."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Lightweight transcript save — no diarization, no speaker detection.

    Two modes (supply exactly one):
    - **audio** (file):          Uploads audio, transcribes via Gemini, saves to DB.
    - **transcript_text** (str): Saves the provided plain text directly to DB.

    Optional fields:
    - **audio_filename** (str):   Display name for text-only transcripts. Defaults to 'manual_entry.txt'.
    - **generate_summary** (bool): If true, generates a preview summary and returns it in the same response.
    """
    if audio is None and not transcript_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Supply either an 'audio' file or 'transcript_text' — not both empty.",
        )

    # ── PATH A: audio file ────────────────────────────────────────────────────
    if audio is not None:
        SUPPORTED_FORMATS = ('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.aac', '.wma', '.webm', '.mp4')
        file_ext = Path(audio.filename).suffix.lower()
        if file_ext not in SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported format '{file_ext}'. Supported: {', '.join(SUPPORTED_FORMATS)}",
            )

        content = await audio.read()
        if len(content) > 1024 * 1024 * 1024:  # 1 GB
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File exceeds 1 GB limit.",
            )

        upload_dir = Path(settings.UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        original_path = upload_dir / audio.filename
        with open(original_path, "wb") as f:
            f.write(content)

        if file_ext != '.wav':
            try:
                from pydub import AudioSegment
                audio_segment = AudioSegment.from_file(str(original_path))
                wav_filename = Path(audio.filename).stem + ".wav"
                wav_path = upload_dir / wav_filename
                audio_segment.export(str(wav_path), format="wav")
                os.remove(str(original_path))
                final_path = str(wav_path)
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Could not convert audio to WAV: {str(e)}",
                )
        else:
            final_path = str(original_path)

        segments = await run_in_threadpool(
            transcribe_simple_audio, final_path, audio.filename, current_user.id, db
        )

        response = {
            "status": "success",
            "message": "Audio transcribed and saved (no diarization).",
            "audio_filename": audio.filename,
            "segment_count": len(segments),
            "segments": segments,
        }

        if generate_summary:
            from types import SimpleNamespace
            mock_t = SimpleNamespace(full_transcript_data=segments)
            summary_result = await run_in_threadpool(generate_preview_summary, [mock_t])
            response["summary"] = summary_result

        return response

    # ── PATH B: raw text from frontend ───────────────────────────────────────
    label = audio_filename or "manual_entry.txt"
    segments = [
        {
            "speaker": "SPEAKER",
            "start_time": 0.0,
            "end_time": 0.0,
            "text": transcript_text.strip(),
        }
    ]
    await run_in_threadpool(save_simple_transcript, db, current_user.id, label, segments)

    response = {
        "status": "success",
        "message": "Transcript text saved to database.",
        "audio_filename": label,
        "segment_count": 1,
        "segments": segments,
    }

    if generate_summary:
        from types import SimpleNamespace
        mock_t = SimpleNamespace(full_transcript_data=segments)
        summary_result = await run_in_threadpool(generate_preview_summary, [mock_t])
        response["summary"] = summary_result

    return response
