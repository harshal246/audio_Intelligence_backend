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
    title: Optional[str] = Form(default=None),
    transcript_text: Optional[str] = Form(default=None),
    audio: Optional[UploadFile] = File(default=None),
    audio_filename: Optional[str] = Form(default=None),
    transcript_id: Optional[str] = Query(default=None, description="Optional ID of an existing transcript to append to"),
    generate_summary: bool = Query(default=False, description="If true, generate a preview summary after saving and return it in the same response."),
    is_last_chunk: bool = Query(default=True, description="Set to false for intermediate chunks — skips Cloudinary upload. Set to true (default) on the final chunk to upload the audio to cloud storage."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Lightweight transcript save — no diarization, no speaker detection.

    Two modes (supply exactly one):
    - **audio** (file):          Uploads audio, transcribes via Gemini, saves to DB.
    - **transcript_text** (str): Saves the provided plain text directly to DB.

    Optional fields:
    - **title** (str):              Optional title. If missing, one is extracted automatically.
    - **audio_filename** (str):     Display name for text-only transcripts. Defaults to 'manual_entry.txt'.
    - **generate_summary** (bool):  If true, generates a summary and saves it to the database.
    - **is_last_chunk** (bool):     Set to false for all intermediate audio chunks — skips
                                    Cloudinary upload (transcription still happens and is appended).
                                    Set to true (or omit) on the final chunk to upload the audio
                                    to cloud storage and save the URL to the transcript record.
    """

    audio_url = None
    final_path = None

    # ── Upload Audio to Cloudinary if provided ─────────────────────────────────
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

        # Upload to Cloudinary ONLY on the last chunk AND if USE_CLOUDINARY is enabled
        if is_last_chunk and settings.USE_CLOUDINARY:
            try:
                from app.services.cloudinary_service import upload_audio_to_cloudinary
                custom_id = f"user_{current_user.id}_{Path(final_path).stem}"

                upload_result = await run_in_threadpool(
                    upload_audio_to_cloudinary,
                    final_path,
                    custom_id
                )
                audio_url = upload_result["url"]
            except Exception as e:
                # Clean up local file on failure
                try:
                    os.remove(final_path)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to upload audio to cloud storage: {str(e)}"
                )
        elif is_last_chunk and not settings.USE_CLOUDINARY:
            # Local storage mode — keep the file on disk; store relative path as the URL
            audio_url = str(final_path)

    # ── Process Transcript Segments ───────────────────────────────────────────
    if transcript_text is not None:
        label = (audio.filename if audio else None) or audio_filename or "manual_entry.txt"
        segments = [
            {
                "speaker": "SPEAKER",
                "start_time": 0.0,
                "end_time": 0.0,
                "text": transcript_text.strip(),
            }
        ]
        result_data = await run_in_threadpool(
            save_simple_transcript, db, current_user.id, label, segments, transcript_id, title or "Untitled Transcript", audio_url
        )
        saved_segments = result_data["segments"]
        saved_transcript_id = result_data["transcript_id"]

        response = {
            "status": "success",
            "message": "Transcript text saved to database." if audio is None else "Audio uploaded and transcript text saved (transcription skipped).",
            "audio_filename": label,
            "transcript_id": saved_transcript_id,
            "segment_count": len(saved_segments),
            "segments": saved_segments,
        }
    elif audio is not None:
        result_data = await run_in_threadpool(
            transcribe_simple_audio, final_path, audio.filename, current_user.id, db, transcript_id, title or "Untitled Transcript", audio_url
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
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either audio file or transcript_text",
        )

    # Clean up local file after successful processing (only when Cloudinary handled storage)
    if final_path and settings.USE_CLOUDINARY:
        try:
            os.remove(final_path)
        except Exception:
            pass

    # ── Summary & Title Extraction ───────────────────────────────────────────
    if generate_summary or title is None:
        import uuid
        from types import SimpleNamespace
        mock_t = SimpleNamespace(full_transcript_data=segments)
        summary_result = await run_in_threadpool(generate_preview_summary, [mock_t])
        
        extracted_title = summary_result["title"]
        
        # Update Transcript title in DB if user did not provide one
        if title is None:
            t_record = db.query(Transcript).filter(Transcript.id == saved_transcript_id).first()
            if t_record:
                t_record.title = extracted_title
                db.commit()
            
            response["extracted_title"] = extracted_title

        # Save Summary to DB if requested
        if generate_summary:
            new_summary = Summary(
                user_id=current_user.id,
                summary_date=None,
                transcript_ids=[uuid.UUID(saved_transcript_id)],
                title=title or extracted_title,
                summary_text=summary_result["summary"]
            )
            db.add(new_summary)
            db.commit()
            db.refresh(new_summary)
            
            response["summary"] = {
                "summary_id": str(new_summary.id),
                "title": new_summary.title,
                "summary_text": new_summary.summary_text
            }

    return response


@router.delete("/{transcript_id}", status_code=status.HTTP_200_OK)
async def delete_transcript(
    transcript_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete a transcript, its embeddings, and its related summaries.
    """
    import uuid
    try:
        t_uuid = uuid.UUID(transcript_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid transcript ID")
        
    transcript = db.query(Transcript).filter(
        Transcript.id == t_uuid,
        Transcript.user_id == current_user.id
    ).first()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
        
    # Delete related embeddings explicitly
    from app.models.transcript_embedding import TranscriptEmbedding
    db.query(TranscriptEmbedding).filter(TranscriptEmbedding.transcript_id == t_uuid).delete()
    
    # Delete related summaries
    summaries = db.query(Summary).filter(Summary.user_id == current_user.id).all()
    for s in summaries:
        if s.transcript_ids and t_uuid in s.transcript_ids:
            db.delete(s)
            
    # Delete the transcript itself
    db.delete(transcript)
    db.commit()
    
    return {"status": "success", "message": "Transcript and related data deleted successfully"}
