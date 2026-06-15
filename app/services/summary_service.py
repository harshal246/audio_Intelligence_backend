# Summary service - handles AI-powered summary generation via Google Gemini
# Also exposes a preview mode that returns formatted plain-text without DB writes
import json
import logging
import re
from datetime import datetime, date
from typing import List, Optional
from uuid import UUID

import google.generativeai as genai
from sqlalchemy.orm import Session

from app.config import settings
from app.models.transcript import Transcript
from app.models.summary import Summary

logger = logging.getLogger(__name__)

# Configure Gemini client once at module load
genai.configure(api_key=settings.GEMINI_API_KEY)


def get_daily_transcripts(db: Session, user_id: UUID, target_date: date) -> List[Transcript]:
    """
    Retrieve all transcript segments for a user on a specific date.

    Args:
        db: Database session
        user_id: UUID of the user
        target_date: Date to retrieve transcripts for

    Returns:
        List of transcript segments ordered by processing time
    """
    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date, datetime.max.time())

    return (
        db.query(Transcript)
        .filter(
            Transcript.user_id == user_id,
            Transcript.processing_timestamp >= start_of_day,
            Transcript.processing_timestamp <= end_of_day,
        )
        .order_by(Transcript.processing_timestamp.asc())
        .all()
    )


def generate_summary_prompt(transcripts: List[Transcript]) -> str:
    """
    Build a structured prompt for Gemini from transcript segments.

    Args:
        transcripts: List of transcript segments

    Returns:
        Formatted prompt string with structured summary instructions
    """
    lines = []
    for t in transcripts:
        if isinstance(t.full_transcript_data, list):
            for seg in t.full_transcript_data:
                speaker = seg.get("speaker", "UNKNOWN")
                text = seg.get("text", "").strip()
                if text:
                    lines.append(f"[{speaker}]: {text}")

    combined_text = "\n".join(lines)

    prompt = f"""You are an intelligent audio analyst. Read the following transcript carefully.

    Return your response as a valid JSON object with EXACTLY the following keys:
    - "key_discussions": 2-3 concise sentences describing the overall purpose and context of the recording, and a brief explanation of every major topic. Do not invent topics.
    - "decisions": Decisions made during the recording. Include ONLY if decisions were actually made.
    - "action_items": Tasks assigned, formatted as 'Task — Owner — Deadline'. Include ONLY if tasks were assigned. Never invent owner or deadline.
    - "highlights": Key facts, numbers, names, risks, or important notes mentioned. Include ONLY if such information exists.
    - "follow_ups": Open questions or unresolved items. Include ONLY if something remains unresolved.

    Rules:
    - Focus on WHAT was discussed, not WHO said it.
    - Do not fabricate any information.
    - Remove filler, greetings, repetitions.
    - Preserve exact numbers, names, and technical terms.
    - For personal notes or journals, summarize naturally without forcing meeting-style sections.
    - Your output must be a valid JSON object. Do not include markdown code fences in your output.

    Transcript:
    {combined_text}
    """
    return prompt


def _extract_json_from_text(text: str) -> dict | None:
    """
    Fallback: extract the first JSON object found in raw text using regex.
    Handles cases where Gemini wraps output in markdown fences or adds preamble.

    Args:
        text: Raw text that may contain a JSON object

    Returns:
        Parsed dict or None if extraction fails
    """
    # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Find the outermost {...} block
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def generate_ai_summary(prompt: str) -> dict:
    """
    Generate AI summary using Google Gemini (gemini-2.5-flash by default).

    Uses the new `google.genai` SDK (replaces deprecated `google.generativeai`).
    max_output_tokens is set high (8192) because gemini-2.5-flash uses budget
    tokens for internal reasoning before producing visible output — a low limit
    causes the JSON response to be truncated mid-string.

    Args:
        prompt: The structured prompt to send to Gemini

    Returns:
        Dictionary with summary sections:
            key_discussions, decisions, action_items, follow_ups, highlights
    """
    empty_result = {
        "key_discussions": "",
        "decisions": "",
        "action_items": "",
        "follow_ups": "",
        "highlights": "",
    }

    try:
        model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            generation_config=genai.GenerationConfig(
                temperature=0.3,           # Low temp for factual, consistent summaries
                max_output_tokens=8192,    # Raised: 2.5-flash uses reasoning tokens too
                response_mime_type="application/json",  # Force JSON output
            ),
        )
        response = model.generate_content(prompt)

        raw_text = response.text.strip()

        # Primary parse
        try:
            summary_data = json.loads(raw_text)
        except json.JSONDecodeError as primary_err:
            logger.warning(
                "Primary JSON parse failed (%s), attempting extraction fallback...", primary_err
            )
            summary_data = _extract_json_from_text(raw_text)
            if summary_data is None:
                logger.error(
                    "Failed to parse Gemini JSON response (fallback also failed): %s", primary_err
                )
                return empty_result

        # Validate, fill missing keys, and ensure values are strings (not lists)
        for key in empty_result:
            if key not in summary_data:
                summary_data[key] = "None identified."
            elif isinstance(summary_data[key], list):
                summary_data[key] = "\n".join(str(item) for item in summary_data[key])
            elif not isinstance(summary_data[key], str):
                summary_data[key] = str(summary_data[key])

        logger.info("Gemini summary generated successfully using model: %s", settings.GEMINI_MODEL)
        return summary_data

    except json.JSONDecodeError as e:
        logger.error("Failed to parse Gemini JSON response: %s", str(e))
        return empty_result
    except Exception as e:
        logger.error("Gemini API call failed: %s", str(e))
        return empty_result


def create_daily_summary(db: Session, user_id: UUID, target_date: date) -> Optional[Summary]:
    """
    Create (or refresh) a daily summary for a user using Gemini AI.

    If a summary already exists for the date, it is updated in-place.

    Args:
        db: Database session
        user_id: UUID of the user
        target_date: Date to generate summary for

    Returns:
        Created/updated Summary object, or None if no transcripts exist
    """
    # Get daily transcripts
    transcripts = get_daily_transcripts(db, user_id, target_date)

    if not transcripts:
        logger.info("No transcripts found for user %s on %s", user_id, target_date)
        return None

    # Build prompt and call Gemini
    prompt = generate_summary_prompt(transcripts)
    summary_data = generate_ai_summary(prompt)

    # Upsert: update existing summary or create new one
    existing_summary = (
        db.query(Summary)
        .filter(Summary.user_id == user_id, Summary.summary_date == target_date)
        .first()
    )

    if existing_summary:
        existing_summary.key_discussions = summary_data["key_discussions"]
        existing_summary.decisions = summary_data["decisions"]
        existing_summary.action_items = summary_data["action_items"]
        existing_summary.follow_ups = summary_data["follow_ups"]
        existing_summary.highlights = summary_data["highlights"]
        db.commit()
        db.refresh(existing_summary)
        logger.info("Updated summary for user %s on %s", user_id, target_date)
        return existing_summary

    # Create new summary record
    summary = Summary(
        user_id=user_id,
        summary_date=target_date,
        key_discussions=summary_data["key_discussions"],
        decisions=summary_data["decisions"],
        action_items=summary_data["action_items"],
        follow_ups=summary_data["follow_ups"],
        highlights=summary_data["highlights"],
    )

    db.add(summary)
    db.commit()
    db.refresh(summary)
    logger.info("Created new summary for user %s on %s", user_id, target_date)
    return summary


def generate_preview_summary(transcripts: List[Transcript]) -> dict:
    """
    Generate a formatted plain-text summary from a list of transcripts using the
    structured analyst prompt. Nothing is written to the database.

    Args:
        transcripts: List of Transcript ORM objects for the requested date

    Returns:
        Dictionary with:
            - "title"  : concise title extracted from the TITLE line (str)
            - "summary": full formatted plain-text summary body (str)
    """
    # Build the combined transcript text
    lines = []
    for t in transcripts:
        if isinstance(t.full_transcript_data, list):
            for seg in t.full_transcript_data:
                speaker = seg.get("speaker", "UNKNOWN")
                text = seg.get("text", "").strip()
                if text:
                    lines.append(f"[{speaker}]: {text}")

    combined_text = "\n".join(lines)

    prompt = f'''You are an intelligent audio analyst. Listen to the entire audio carefully.
 
Return your response in EXACTLY this format (no extra text before or after):
 
TITLE: <a concise title, maximum 5 words, no punctuation, no markdown>
 
---SUMMARY---
 
Overview
Write 2-3 concise sentences describing the overall purpose and context of the recording.
 
Key Topics
- Topic: Brief explanation
 
(List every major topic. Do not invent topics.)
 
Decisions Made
- Decision
 
(OMIT this entire section, including the heading, if no decisions were made.)
 
Action Items
- Task — Owner — Deadline
 
(OMIT this entire section, including the heading, if no tasks were assigned. Never invent owner or deadline.)
 
Important Notes
- Key facts, numbers, names, risks mentioned.
 
(OMIT this entire section, including the heading, if no such information exists.)
 
Follow-ups
- Open questions or unresolved items.
 
(OMIT this entire section, including the heading, if nothing remains unresolved.)
 
Rules:
- Focus on WHAT was discussed, not WHO said it.
- Do not fabricate any information.
- Do not write "None", "N/A", or empty bullet points under any section.
- Remove filler, greetings, repetitions.
- Preserve exact numbers, names, and technical terms.
- For personal notes or journals, summarize naturally without forcing meeting-style sections.
Transcript:
{combined_text}'''

    try:
        model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            generation_config=genai.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8192,
            ),
        )
        response = model.generate_content(prompt)
        raw_text = response.text.strip()

        # Extract TITLE from the first line ("TITLE: Some Title Here")
        title = "Untitled"
        body = raw_text
        first_line = raw_text.splitlines()[0] if raw_text else ""
        if first_line.upper().startswith("TITLE:"):
            title = first_line[len("TITLE:"):].strip()
            # Remove the title line from the body to avoid duplication
            body = raw_text[len(first_line):].strip()

        logger.info("Preview summary generated — title: %s", title)
        return {"title": title, "summary": body}

    except Exception as e:
        logger.error("Preview summary generation failed: %s", str(e))
        raise
