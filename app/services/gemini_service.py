# Gemini transcription service - handles speech-to-text transcription using Google Gemini
import logging
from typing import Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


def transcribe_audio_gemini(audio_path: str) -> Dict:
    """
    Transcribe audio using Google Gemini.

    Args:
        audio_path: Path to the audio file

    Returns:
        Dictionary containing transcription result with segments
    """
    try:
        import json
        import re
        from google import genai
        from google.genai import types

        # Configure Gemini client
        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        # Create prompt for transcription
        prompt = """
        You are an audio transcription assistant. Transcribe the following audio file.
        Return the transcription as a JSON object matching the required schema.

        Note: Please provide the actual transcribed text and reasonable timestamps for each segment in total seconds.
        Additionally, perform speaker diarization by identifying different speakers in the conversation and labeling 
        them consistently (e.g., "SPEAKER_1", "SPEAKER_2", etc.) in the "speaker" field.
        CRITICAL: The "start" and "end" timestamps MUST be valid floating point values in total seconds
        (e.g., 60.5 for 1 minute and 0.5 seconds). DO NOT use formatted strings or multiple decimals like 1.0.66.
        """

        logger.info("Uploading audio file to Gemini: %s", audio_path)
        audio_file_obj = client.files.upload(file=audio_path)

        logger.info("Generating transcription with Gemini for audio: %s", audio_path)
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[prompt, audio_file_obj],
            config=types.GenerateContentConfig(
                temperature=0.1,
                # Raised to model maximum — long audio can produce thousands of tokens.
                # Gemini 2.5 Flash supports up to 65 536 output tokens.
                max_output_tokens=65536,
                response_mime_type="application/json",
                response_schema={
                    "type": "object",
                    "properties": {
                        "language": {"type": "string"},
                        "segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "speaker": {
                                        "type": "string",
                                        "description": "The speaker identifier label, e.g. SPEAKER_1, SPEAKER_2",
                                    },
                                    "start": {
                                        "type": "number",
                                        "description": "Start time in total seconds as a float (e.g. 60.5 for 1m0.5s)",
                                    },
                                    "end": {
                                        "type": "number",
                                        "description": "End time in total seconds as a float (e.g. 63.2 for 1m3.2s)",
                                    },
                                },
                                "required": ["text", "speaker", "start", "end"],
                            },
                        },
                    },
                    "required": ["language", "segments"],
                },
            )
        )

        raw_text = response.text

        # ── Primary parse ──────────────────────────────────────────────────────
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError as primary_err:
            logger.warning(
                "Primary JSON parse failed (%s) — attempting segment recovery...", primary_err
            )
            # ── Truncation-recovery: extract every complete segment object ──────
            # Find all complete JSON objects that contain a "text" key.
            # This handles any field order (text/speaker/start/end).
            brace_depth = 0
            obj_start = None
            recovered_segments = []
            for i, ch in enumerate(raw_text):
                if ch == '{':
                    if brace_depth == 1 and obj_start is None:
                        obj_start = i  # start of a segment-level object
                    elif brace_depth == 0:
                        pass  # top-level object, skip
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
                    if brace_depth == 1 and obj_start is not None:
                        candidate = raw_text[obj_start:i+1]
                        try:
                            seg = json.loads(candidate)
                            if "text" in seg:
                                recovered_segments.append({
                                    "text": seg.get("text", ""),
                                    "speaker": seg.get("speaker", "SPEAKER"),
                                    "start": float(seg.get("start", 0)),
                                    "end": float(seg.get("end", 0)),
                                })
                        except (json.JSONDecodeError, ValueError):
                            pass
                        obj_start = None

            if recovered_segments:
                logger.warning(
                    "Recovered %d segment(s) from truncated Gemini response.",
                    len(recovered_segments),
                )
                result = {"language": "unknown", "segments": recovered_segments}
            else:
                logger.error(
                    "Failed to parse Gemini response and recovery found 0 segments.\n"
                    "Response tail: %s", raw_text[-300:]
                )
                client.files.delete(name=audio_file_obj.name)
                raise primary_err

        # Keep speaker fields returned by Gemini

        logger.info(
            "Gemini transcription complete — %d segment(s), language: %s",
            len(result.get("segments", [])),
            result.get("language", "unknown"),
        )

        client.files.delete(name=audio_file_obj.name)
        return result

    except Exception as e:
        logger.error("Gemini transcription failed: %s", str(e))
        raise


def get_gemini_device_config():
    """
    Get device configuration for Gemini (CPU only, as Gemini runs in the cloud).

    Returns:
        Dictionary with device configuration
    """
    return {
        "device": "cpu",
        "compute_type": "float32",
        "batch_size": 1,
    }


def cleanup_gemini_resources():
    """
    Cleanup Gemini resources (no GPU memory to free for cloud-based Gemini).
    """
    logger.info("Gemini cleanup - no local resources to free")
