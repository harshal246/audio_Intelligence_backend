# Transcript service - orchestrates the complete audio processing pipeline
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
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
from app.services.gemini_service import transcribe_audio_gemini
from app.services.diarization_service import diarize_audio, assign_speakers_to_segments


def process_audio_pipeline(audio_path: str, user_id: UUID, db: Session, audio_filename: str, use_gemini: bool = False) -> List[Dict]:
    """
    Complete audio processing pipeline:
    1. Transcribe audio with WhisperX or Gemini
    2. Align transcript for improved timestamps (WhisperX only)
    3. Cleanup GPU memory (WhisperX only)
    4. Perform speaker diarization
    5. Assign speakers to transcript segments
    6. Store in database
    
    Args:
        audio_path: Path to the audio file
        user_id: UUID of the user who uploaded the audio
        db: Database session
        audio_filename: Original filename of the uploaded audio
        use_gemini: Whether to use Gemini for transcription instead of WhisperX
    
    Returns:
        List of transcript segments with speaker information
    """
    # Step 1: Transcribe audio
    if use_gemini:
        logger.info("Using Gemini for transcription")
        result = transcribe_audio_gemini(audio_path)
        # Gemini doesn't have alignment, so we skip step 2
        # Gemini doesn't use GPU memory, so we skip step 3
    else:
        logger.info("Using WhisperX for transcription")
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


def save_simple_transcript(
    db: Session,
    user_id: UUID,
    audio_filename: str,
    segments: List[Dict],
    transcript_id: str = None,
    title: str = "Untitled Transcript",
    audio_url: str = None,
    trigger_embeddings: bool = True,
) -> Dict:
    """
    Save a transcript (already built externally) to the database without
    any diarization or speaker-assignment step. Appends if transcript_id is provided.

    Args:
        db:             Active SQLAlchemy session
        user_id:        Authenticated user's UUID
        audio_filename: Label to store (original filename or a display name)
        segments:       List of {speaker, start_time, end_time, text} dicts
        transcript_id:  Optional ID of an existing transcript to append to
        trigger_embeddings: If true, triggers embedding generation synchronously.

    Returns:
        Dict containing transcript_id and the full segments list
    """
    transcript = None
    if transcript_id:
        transcript = db.query(Transcript).filter(
            Transcript.id == transcript_id,
            Transcript.user_id == user_id
        ).first()

    if transcript:
        existing_data = transcript.full_transcript_data or []
        transcript.full_transcript_data = existing_data + segments
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(transcript, "full_transcript_data")
        db.commit()
        logger.info("Appended %d segment(s) to transcript %s", len(segments), transcript_id)

        # Re-generate embeddings for the full updated transcript
        if trigger_embeddings:
            _trigger_embeddings(db, transcript)

        return {"transcript_id": str(transcript.id), "segments": transcript.full_transcript_data}
    else:
        transcript = Transcript(
            user_id=user_id,
            title=title,
            audio_filename=audio_filename,
            audio_url=audio_url,
            full_transcript_data=segments,
        )
        db.add(transcript)
        db.commit()
        logger.info("Simple transcript saved for user %s — %d segment(s)", user_id, len(segments))

        # Generate embeddings for the new transcript
        if trigger_embeddings:
            _trigger_embeddings(db, transcript)

        return {"transcript_id": str(transcript.id), "segments": segments}


def _seconds_to_timestamp(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    elif m > 0:
        return f"{m}m {s:02d}s"
    else:
        return f"{s}s"


def _trigger_embeddings(db: Session, transcript: Transcript) -> None:
    """
    Fire-and-forget helper: build the plain text from a transcript's segments
    and call the embedding service to chunk + vectorise it.
    Failures are logged but never propagate — embeddings are best-effort.
    """
    try:
        from app.services.embedding_service import save_embeddings
        import datetime
        segments = transcript.full_transcript_data or []
        
        text_parts = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text", "").strip()
            if not text:
                continue
            ts = seg.get("start_time")
            if ts is not None:
                text_parts.append(f"[{_seconds_to_timestamp(float(ts))}] {text}")
            else:
                text_parts.append(text)
        
        full_text = " ".join(text_parts)
        
        if full_text:
            dt = transcript.processing_timestamp or datetime.datetime.now(datetime.timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            title_str = transcript.title or transcript.audio_filename or "Untitled"
            metadata_prefix = f"[Transcript Date: {date_str}, Title: {title_str}]"
            
            save_embeddings(db, transcript.id, transcript.user_id, full_text, metadata_prefix=metadata_prefix)
    except Exception as e:
        logger.warning("Embedding generation skipped for transcript %s: %s", transcript.id, e)


def transcribe_simple_audio(
    audio_path: str,
    audio_filename: str,
    user_id: UUID,
    db: Session,
    transcript_id: str = None,
    title: str = "Untitled Transcript",
    audio_url: str = None,
    trigger_embeddings: bool = True,
) -> Dict:
    """
    Transcribe audio with Gemini only — no diarization, no speaker labels.
    Automatically chunks audio longer than 15 minutes to avoid Gemini's
    output token limit.

    Args:
        audio_path:     Path to the audio file to transcribe
        audio_filename: Original filename for the DB record
        user_id:        Authenticated user's UUID
        db:             Active SQLAlchemy session
        transcript_id:  Optional ID of an existing transcript to append to
        trigger_embeddings: If true, triggers embedding generation synchronously.

    Returns:
        Dict containing transcript_id and the full segments list
    """
    from app.services.preprocessing_service import get_audio_duration_minutes, chunk_audio, cleanup_chunks

    CHUNK_THRESHOLD_MINUTES = 15  # Chunk audio longer than this

    logger.info("Simple Gemini transcription (no diarization) for: %s", audio_filename)

    try:
        duration_minutes = get_audio_duration_minutes(audio_path)
        logger.info("Audio duration: %.1f minutes", duration_minutes)

        if duration_minutes > CHUNK_THRESHOLD_MINUTES:
            # ── Chunked transcription for long audio ─────────────────────────
            logger.info(
                "Audio exceeds %d min — splitting into chunks for Gemini.",
                CHUNK_THRESHOLD_MINUTES,
            )
            chunks = chunk_audio(audio_path, chunk_minutes=CHUNK_THRESHOLD_MINUTES)
            logger.info("Created %d chunk(s)", len(chunks))

            all_segments = []
            for idx, (chunk_path, offset_seconds) in enumerate(chunks):
                logger.info(
                    "Transcribing chunk %d/%d (offset=%.1fs): %s",
                    idx + 1, len(chunks), offset_seconds, chunk_path,
                )
                try:
                    chunk_result = transcribe_audio_gemini(chunk_path)
                except Exception as e:
                    logger.error("Chunk %d transcription failed: %s", idx + 1, e)
                    continue  # Skip failed chunk, keep the rest

                for seg in chunk_result.get("segments", []):
                    text = seg.get("text", "").strip()
                    if not text:
                        continue
                    all_segments.append({
                        "speaker": seg.get("speaker", "SPEAKER"),
                        "start_time": seg.get("start", 0.0) + offset_seconds,
                        "end_time": seg.get("end", 0.0) + offset_seconds,
                        "text": text,
                    })

            # Clean up chunk files
            cleanup_chunks()
            segments = all_segments
        else:
            # ── Single-shot transcription for short audio ────────────────────
            result = transcribe_audio_gemini(audio_path)
            segments = []
            for seg in result.get("segments", []):
                text = seg.get("text", "").strip()
                if not text:
                    continue
                segments.append({
                    "speaker": seg.get("speaker", "SPEAKER"),
                    "start_time": seg.get("start", 0.0),
                    "end_time": seg.get("end", 0.0),
                    "text": text,
                })
    finally:
        # Always clean up the original audio file
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
                logger.info("Deleted raw audio file post-transcription: %s", audio_path)
            except Exception as e:
                logger.error("Failed to delete raw audio file %s: %s", audio_path, e)

    return save_simple_transcript(db, user_id, audio_filename, segments, transcript_id, title, audio_url, trigger_embeddings=trigger_embeddings)



def trigger_embeddings_background(transcript_id: str) -> None:
    """
    Background task to fetch the transcript and trigger its embeddings
    using a new DB session to prevent cross-thread session issues.
    """
    logger.info("Starting background embedding generation for transcript %s", transcript_id)
    from app.database.db import SessionLocal
    from app.models.transcript import Transcript
    import uuid

    db = SessionLocal()
    try:
        t_uuid = uuid.UUID(transcript_id) if isinstance(transcript_id, str) else transcript_id
        transcript = db.query(Transcript).filter(Transcript.id == t_uuid).first()
        if transcript:
            _trigger_embeddings(db, transcript)
            logger.info("Successfully completed background embedding generation for transcript %s", transcript_id)
        else:
            logger.error("Transcript %s not found in background task", transcript_id)
    except Exception as e:
        logger.exception("Failed generating embeddings in background for transcript %s", transcript_id)
    finally:
        db.close()
