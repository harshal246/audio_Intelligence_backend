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
from app.utils.auth import get_default_user

router = APIRouter(prefix="/transcribe", tags=["transcript"])

class ConnectionManager:
    def __init__(self):
        # Maps user_id to a list of active websocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections and websocket in self.active_connections[user_id]:
            self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_json_message(self, data: dict, user_id: str):
        if user_id in self.active_connections:
            # Need to create a copy of the list to safely iterate while items might be removed
            for connection in list(self.active_connections[user_id]):
                try:
                    await connection.send_json(data)
                except Exception:
                    # Ignore errors like disconnected clients
                    pass

manager = ConnectionManager()

async def background_audio_processing(audio_path: str, user_id, audio_filename: str, use_gemini: bool = False):
    """Wrapper to run the audio pipeline in the background with a dedicated DB session."""
    def _process():
        db = SessionLocal()
        try:
            process_audio_pipeline(audio_path, user_id, db, audio_filename, use_gemini)
            return True, None
        except Exception as e:
            print(f"Background audio processing failed: {str(e)}")
            return False, str(e)
        finally:
            db.close()
            
    success, error = await run_in_threadpool(_process)
    
    # Notify via WebSocket
    if success:
        await manager.send_json_message(
            {"status": "completed", "filename": audio_filename, "use_gemini": use_gemini},
            str(user_id)
        )
    else:
        await manager.send_json_message(
            {"status": "failed", "filename": audio_filename, "use_gemini": use_gemini, "error": error},
            str(user_id)
        )

async def handle_audio_upload(
    audio: UploadFile,
    background_tasks: BackgroundTasks,
    current_user: User,
    use_gemini: bool
):
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
        audio.filename,
        use_gemini
    )
    
    return {
        "status": "processing",
        "message": f"Audio uploaded successfully. Transcription ({'Gemini' if use_gemini else 'WhisperX'}) is running in the background.",
        "audio_filename": audio.filename
    }

@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def transcribe_audio(
    audio: UploadFile,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_default_user),
    db: Session = Depends(get_db),
    use_gemini: bool = False
):
    """
    Upload and transcribe audio file.
    By default, uses WhisperX + diarization pipeline.
    """
    return await handle_audio_upload(audio, background_tasks, current_user, use_gemini)

@router.post("/gemini", status_code=status.HTTP_202_ACCEPTED)
async def transcribe_audio_gemini_endpoint(
    audio: UploadFile,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_default_user),
    db: Session = Depends(get_db)
):
    """
    Upload and transcribe audio file specifically with Gemini.
    """
    return await handle_audio_upload(audio, background_tasks, current_user, use_gemini=True)

@router.get("/", status_code=status.HTTP_200_OK)
async def get_transcripts(
    current_user: User = Depends(get_default_user),
    db: Session = Depends(get_db)
):
    """
    Get all transcripts for the current user.
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

@router.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """
    WebSocket endpoint for real-time transcription status updates.
    The client_id should ideally be the user's ID or a unique session token.
    For production, you'd want to validate the token here as well.
    """
    await manager.connect(websocket, client_id)
    try:
        while True:
            # Keep the connection open and wait for updates
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, client_id)


@router.post("/simple", status_code=status.HTTP_201_CREATED)
async def transcribe_simple(
    audio: Optional[UploadFile] = File(default=None),
    transcript_text: Optional[str] = Form(default=None),
    audio_filename: Optional[str] = Form(default=None),
    generate_summary: bool = Query(default=False, description="If true, generate a preview summary after saving and return it in the same response."),
    current_user: User = Depends(get_default_user),
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
