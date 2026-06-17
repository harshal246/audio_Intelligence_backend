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


def get_transcripts_by_ids(db: Session, user_id: UUID, transcript_ids: List[UUID]) -> List[Transcript]:
    """
    Retrieve specific transcript segments for a user.
    """
    return (
        db.query(Transcript)
        .filter(
            Transcript.user_id == user_id,
            Transcript.id.in_(transcript_ids),
        )
        .order_by(Transcript.processing_timestamp.asc())
        .all()
    )


def create_daily_summary(db: Session, user_id: UUID, target_date: date) -> Optional[Summary]:
    """
    Create (or refresh) a daily summary for a user using Gemini AI.
    """
    transcripts = get_daily_transcripts(db, user_id, target_date)

    if not transcripts:
        logger.info("No transcripts found for user %s on %s", user_id, target_date)
        return None

    result = generate_preview_summary(transcripts)

    existing_summary = (
        db.query(Summary)
        .filter(Summary.user_id == user_id, Summary.summary_date == target_date)
        .first()
    )

    if existing_summary:
        existing_summary.title = result["title"]
        existing_summary.summary_text = result["summary"]
        db.commit()
        db.refresh(existing_summary)
        logger.info("Updated daily summary for user %s on %s", user_id, target_date)
        return existing_summary

    summary = Summary(
        user_id=user_id,
        summary_date=target_date,
        transcript_ids=None,
        title=result["title"],
        summary_text=result["summary"],
    )

    db.add(summary)
    db.commit()
    db.refresh(summary)
    logger.info("Created new daily summary for user %s on %s", user_id, target_date)
    return summary


def create_custom_summary(db: Session, user_id: UUID, transcript_ids: List[UUID]) -> Optional[Summary]:
    """
    Create a custom summary based on specific transcript IDs.
    """
    transcripts = get_transcripts_by_ids(db, user_id, transcript_ids)
    if not transcripts:
        return None

    result = generate_preview_summary(transcripts)

    summary = Summary(
        user_id=user_id,
        summary_date=None,
        transcript_ids=transcript_ids,
        title=result["title"],
        summary_text=result["summary"],
    )

    db.add(summary)
    db.commit()
    db.refresh(summary)
    logger.info("Created custom summary for user %s with %d transcripts", user_id, len(transcript_ids))
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

        # Extract TITLE robustly using regex
        title = "Untitled"
        body = raw_text
        
        # Look for TITLE: optionally surrounded by markdown bold **
        title_match = re.search(r"^\s*(?:\*\*)?TITLE:\s*(?:\*\*)?(.*)$", raw_text, re.MULTILINE | re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
            # Remove the entire title line from the body to avoid duplication
            body = raw_text.replace(title_match.group(0), "").strip()
        else:
            # Fallback: Gemini sometimes completely drops the "TITLE:" prefix
            # Assume the first non-empty line is the title if it's not a section header
            lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
            if lines:
                first_line = lines[0]
                if not first_line.upper().startswith("OVERVIEW") and not first_line.startswith("---"):
                    title = first_line.replace("**", "") # Remove any stray bold tags
                    body = "\n".join(lines[1:]).strip()
            
        # Clean up the ---SUMMARY--- marker if it exists
        body = re.sub(r"^\s*---SUMMARY---\s*", "", body, flags=re.MULTILINE).strip()

        logger.info("Preview summary generated — title: %s", title)
        return {"title": title, "summary": body}

    except Exception as e:
        logger.error("Preview summary generation failed: %s", str(e))
        raise
