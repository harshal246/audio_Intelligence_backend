# Gemini transcription service - handles speech-to-text transcription using Google Gemini
import time
import logging
import io
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
        import google.generativeai as genai

        # Configure Gemini client
        genai.configure(api_key=settings.GEMINI_API_KEY)

        # Create a Gemini model for transcription
        model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            generation_config={
                "temperature": 0.1,
                # Raised to model maximum — long audio can produce thousands of tokens.
                # Gemini 2.5 Flash supports up to 65 536 output tokens.
                "max_output_tokens": 65536,
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string"},
                        "segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "start": {
                                        "type": "number",
                                        "description": "Start time in total seconds as a float (e.g. 60.5 for 1m0.5s)",
                                    },
                                    "end": {
                                        "type": "number",
                                        "description": "End time in total seconds as a float (e.g. 63.2 for 1m3.2s)",
                                    },
                                },
                                "required": ["text", "start", "end"],
                            },
                        },
                    },
                    "required": ["language", "segments"],
                },
            },
        )

        # Create prompt for transcription
        prompt = """
        You are an audio transcription assistant. Transcribe the following audio file.
        Return the transcription as a JSON object matching the required schema.

        Note: Since this is a transcription task, please provide the actual transcribed text
        and reasonable timestamps for each segment in total seconds. Do NOT include speaker information.
        CRITICAL: The "start" and "end" timestamps MUST be valid floating point values in total seconds
        (e.g., 60.5 for 1 minute and 0.5 seconds). DO NOT use formatted strings or multiple decimals like 1.0.66.
        """

        logger.info("Uploading audio file to Gemini: %s", audio_path)
        audio_file_obj = genai.upload_file(path=audio_path)

        logger.info("Generating transcription with Gemini for audio: %s", audio_path)
        response = model.generate_content([prompt, audio_file_obj])

        raw_text = response.text

        # ── Primary parse ──────────────────────────────────────────────────────
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError as primary_err:
            logger.warning(
                "Primary JSON parse failed (%s) — attempting segment recovery...", primary_err
            )
            # ── Truncation-recovery: extract every complete segment object ──────
            # Matches complete {"text": "...", "start": N, "end": N} blocks.
            segment_pattern = re.compile(
                r'\{\s*"text"\s*:\s*"(?P<text>(?:[^"\\]|\\.)*)"\s*,'
                r'\s*"start"\s*:\s*(?P<start>-?\d+(?:\.\d+)?)\s*,'
                r'\s*"end"\s*:\s*(?P<end>-?\d+(?:\.\d+)?)\s*\}',
                re.DOTALL,
            )
            recovered_segments = []
            for m in segment_pattern.finditer(raw_text):
                recovered_segments.append({
                    "text": m.group("text"),
                    "start": float(m.group("start")),
                    "end": float(m.group("end")),
                })

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
                genai.delete_file(audio_file_obj.name)
                raise primary_err

        # Strip any speaker fields Gemini may have hallucinated
        for segment in result.get("segments", []):
            segment.pop("speaker", None)

        logger.info(
            "Gemini transcription complete — %d segment(s), language: %s",
            len(result.get("segments", [])),
            result.get("language", "unknown"),
        )

        genai.delete_file(audio_file_obj.name)
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
