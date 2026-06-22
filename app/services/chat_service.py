import json
import logging
import re
from datetime import datetime, date, timedelta
from typing import List, Optional
from uuid import UUID

import google.generativeai as genai
from sqlalchemy.orm import Session

from app.config import settings
from app.models.chat import ChatSession, ChatMessage
from app.models.transcript import Transcript
from app.models.summary import Summary

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)

ANALYSIS_PROMPT = """You are an assistant that analyzes questions about a user's audio transcripts and summaries. Given the question, extract what data is needed.

Return ONLY valid JSON with these fields:
{
  "date_from": "YYYY-MM-DD or null if not a date-specific query",
  "date_to": "YYYY-MM-DD or null",
  "keywords": ["list of search keywords or empty array"],
  "needs_transcripts": true or false,
  "needs_summaries": true or false
}

Assume the current date is {today}.

Examples:
Q: "what did I do on the 17th?"
A: {{"date_from": "{this_month_17}", "date_to": "{this_month_17}", "keywords": [], "needs_transcripts": true, "needs_summaries": true}}

Q: "tell me about my meeting with John"
A: {{"date_from": null, "date_to": null, "keywords": ["John", "meeting"], "needs_transcripts": true, "needs_summaries": false}}

Q: "summarize everything from last week"
A: {{"date_from": "{last_week_start}", "date_to": "{last_week_end}", "keywords": [], "needs_transcripts": true, "needs_summaries": true}}

Q: "what decisions were made recently?"
A: {{"date_from": null, "date_to": null, "keywords": ["decisions"], "needs_transcripts": true, "needs_summaries": true}}

Now analyze this question:
Q: "{question}"
A:"""

ANSWER_PROMPT = """You are a helpful assistant that answers questions about a user's personal audio transcripts and summaries. Be conversational and concise.

Today's date: {today}

RELEVANT TRANSCRIPTS:
{transcripts_text}

RELEVANT SUMMARIES:
{summaries_text}

CONVERSATION HISTORY (most recent first):
{history}

Question: {question}

Answer the question using ONLY the data above. If the data doesn't contain what is asked, say so politely. Do not fabricate information."""


def _today() -> date:
    return date.today()


def _format_transcripts(transcripts: List[Transcript]) -> str:
    lines = []
    for t in transcripts:
        ts = t.processing_timestamp.strftime("%Y-%m-%d %H:%M") if t.processing_timestamp else "unknown"
        lines.append(f"--- Transcript: {t.title} ({ts}) ---")
        if isinstance(t.full_transcript_data, list):
            for seg in t.full_transcript_data:
                speaker = seg.get("speaker", "UNKNOWN")
                text = seg.get("text", "").strip()
                if text:
                    lines.append(f"[{speaker}]: {text}")
        lines.append("")
    return "\n".join(lines) or "(no transcripts found)"


def _format_summaries(summaries: List[Summary]) -> str:
    lines = []
    for s in summaries:
        sd = s.summary_date.strftime("%Y-%m-%d") if s.summary_date else "custom"
        lines.append(f"--- Summary ({sd}): {s.title or 'Untitled'} ---")
        lines.append(s.summary_text or "")
        lines.append("")
    return "\n".join(lines) or "(no summaries found)"


def _format_history(messages: List[ChatMessage], max_messages: int = 6) -> str:
    recent = messages[-max_messages:]
    lines = []
    for m in recent:
        label = "User" if m.role == "user" else "Assistant"
        lines.append(f"{label}: {m.content}")
    return "\n".join(lines) or "(no previous conversation)"


def _analyze_question(question: str) -> dict:
    today = _today()
    first_of_month = today.replace(day=1)
    last_day = (first_of_month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    this_month_17 = today.replace(day=min(17, last_day.day)).isoformat()
    last_week_start = (today - timedelta(days=today.weekday() + 7)).isoformat()
    last_week_end = (today - timedelta(days=today.weekday() + 1)).isoformat()

    prompt = ANALYSIS_PROMPT.format(
        today=today.isoformat(),
        this_month_17=this_month_17,
        last_week_start=last_week_start,
        last_week_end=last_week_end,
        question=question,
    )

    model = genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL,
        generation_config=genai.GenerationConfig(
            temperature=0.0,
            max_output_tokens=512,
        ),
    )
    resp = model.generate_content(prompt)
    raw = resp.text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def _matches_keywords(text: str, keywords: list) -> bool:
    if not keywords:
        return True
    lower_text = text.lower()
    return any(kw.lower() in lower_text for kw in keywords)


def _filter_transcripts_by_keywords(transcripts: List[Transcript], keywords: list) -> List[Transcript]:
    if not keywords:
        return transcripts
    matched = []
    for t in transcripts:
        if not isinstance(t.full_transcript_data, list):
            continue
        seg_texts = [seg.get("text", "") for seg in t.full_transcript_data if seg.get("text")]
        combined = " ".join(seg_texts)
        if _matches_keywords(combined, keywords):
            matched.append(t)
    return matched


def _fetch_relevant_data(
    db: Session,
    user_id: UUID,
    analysis: dict,
) -> tuple:
    needs_transcripts = analysis.get("needs_transcripts", True)
    needs_summaries = analysis.get("needs_summaries", True)
    date_from = analysis.get("date_from")
    date_to = analysis.get("date_to")
    keywords = analysis.get("keywords", [])

    transcripts = []
    summaries = []

    if needs_transcripts:
        base_q = db.query(Transcript).filter(Transcript.user_id == user_id)
        if date_from:
            base_q = base_q.filter(Transcript.processing_timestamp >= datetime.fromisoformat(date_from))
        if date_to:
            base_q = base_q.filter(Transcript.processing_timestamp <= datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59))

        fetch_limit = 100 if keywords else 20
        transcripts = base_q.order_by(Transcript.processing_timestamp.desc()).limit(fetch_limit).all()

        if keywords:
            transcripts = _filter_transcripts_by_keywords(transcripts, keywords)

    if needs_summaries:
        summary_q = db.query(Summary).filter(Summary.user_id == user_id)
        if date_from:
            summary_q = summary_q.filter(Summary.summary_date >= datetime.fromisoformat(date_from))
        if date_to:
            summary_q = summary_q.filter(Summary.summary_date <= datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59))

        summaries = summary_q.order_by(Summary.created_at.desc()).limit(20).all()

        if keywords:
            summaries = [s for s in summaries if _matches_keywords(s.summary_text or "", keywords)]

    return transcripts, summaries


def _generate_answer(
    question: str,
    transcripts: List[Transcript],
    summaries: List[Summary],
    history: List[ChatMessage],
) -> str:
    today = _today().isoformat()
    transcripts_text = _format_transcripts(transcripts)
    summaries_text = _format_summaries(summaries)
    history_text = _format_history(history)

    prompt = ANSWER_PROMPT.format(
        today=today,
        transcripts_text=transcripts_text,
        summaries_text=summaries_text,
        history=history_text,
        question=question,
    )

    model = genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL,
        generation_config=genai.GenerationConfig(
            temperature=0.3,
            max_output_tokens=2048,
        ),
    )
    resp = model.generate_content(prompt)
    return resp.text.strip()


def ask_question(
    db: Session,
    user_id: UUID,
    question: str,
    session_id: Optional[UUID] = None,
) -> dict:
    if session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        ).first()
        if not session:
            session = ChatSession(user_id=user_id, title=question[:50])
            db.add(session)
            db.commit()
            db.refresh(session)
    else:
        session = ChatSession(user_id=user_id, title=question[:50])
        db.add(session)
        db.commit()
        db.refresh(session)

    user_msg = ChatMessage(session_id=session.id, role="user", content=question)
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    try:
        analysis = _analyze_question(question)
        logger.info("Question analysis for session %s: %s", session.id, analysis)
    except Exception as e:
        logger.warning("Question analysis failed, falling back to full fetch: %s", e)
        analysis = {
            "date_from": None,
            "date_to": None,
            "keywords": [],
            "needs_transcripts": True,
            "needs_summaries": True,
        }

    transcripts, summaries = _fetch_relevant_data(db, user_id, analysis)

    try:
        answer = _generate_answer(question, transcripts, summaries, history)
    except Exception as e:
        logger.error("Answer generation failed: %s", e)
        answer = "Sorry, I couldn't generate an answer right now. Please try again."

    assistant_msg = ChatMessage(session_id=session.id, role="assistant", content=answer)
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    history.append(assistant_msg)

    return {
        "session_id": session.id,
        "answer": answer,
        "history": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in history
        ],
    }
