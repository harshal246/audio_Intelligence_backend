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


from app.models.transcript import Transcript
from app.models.summary import Summary

@router.get("/", status_code=status.HTTP_200_OK)
async def get_transcripts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retrieve all transcripts for the current user.
    Includes the transcript's summary IF a custom summary was generated 
    specifically (and only) for this transcript.
    """
    transcripts = db.query(Transcript).filter(
        Transcript.user_id == current_user.id
    ).order_by(Transcript.processing_timestamp.desc()).all()

    summaries = db.query(Summary).filter(
        Summary.user_id == current_user.id,
        Summary.transcript_ids.isnot(None)
    ).all()
    
    summary_map = {}
    for s in summaries:
        if s.transcript_ids and len(s.transcript_ids) == 1:
            summary_map[str(s.transcript_ids[0])] = {
                "summary_id": str(s.id),
                "title": s.title,
                "summary_text": s.summary_text,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }

    result = []
    for t in transcripts:
        t_id = str(t.id)
        result.append({
            "transcript_id": t_id,
            "title": t.title,
            "processing_timestamp": t.processing_timestamp.isoformat() if t.processing_timestamp else None,
            "summary": summary_map.get(t_id)
        })

    return {
        "status": "success",
        "count": len(result),
        "transcripts": result
    }


@router.post("/simple", status_code=status.HTTP_201_CREATED)
async def transcribe_simple(
    title: str = Form(...),
    transcript_text: str = Form(...),
    audio: Optional[UploadFile] = File(default=None),
    audio_filename: Optional[str] = Form(default=None),
    transcript_id: Optional[str] = Query(default=None, description="Optional ID of an existing transcript to append to"),
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

        result_data = await run_in_threadpool(
            transcribe_simple_audio, final_path, audio.filename, current_user.id, db, transcript_id, title
        )
        segments = result_data["segments"]
        saved_transcript_id = result_data["transcript_id"]

        response = {
            "status": "success",
            "message": "Audio transcribed and saved (no diarization).",
            "audio_filename": audio.filename,
            "transcript_id": saved_transcript_id,
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
    result_data = await run_in_threadpool(save_simple_transcript, db, current_user.id, label, segments, transcript_id, title)
    saved_segments = result_data["segments"]
    saved_transcript_id = result_data["transcript_id"]

    response = {
        "status": "success",
        "message": "Transcript text saved to database.",
        "audio_filename": label,
        "transcript_id": saved_transcript_id,
        "segment_count": len(saved_segments),
        "segments": saved_segments,
    }

    if generate_summary:
        from types import SimpleNamespace
        mock_t = SimpleNamespace(full_transcript_data=segments)
        summary_result = await run_in_threadpool(generate_preview_summary, [mock_t])
        response["summary"] = summary_result

    return response
