"""
Chat service — RAG-powered Q&A over a user's audio transcripts.

Flow per question:
  1. Store user message in DB.
  2. Perform semantic vector search to retrieve the most relevant transcript
     chunks (replaces the old date-analysis + hard limit approach).
  3. Feed retrieved chunks + conversation history to Gemini to generate an answer.
  4. Store assistant message in DB and return the full session history.
"""
import logging
from datetime import date
from typing import List, Optional
from uuid import UUID

from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from app.config import settings
from app.models.chat import ChatSession, ChatMessage

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────────────────────────

ANSWER_PROMPT = """You are a helpful assistant that answers questions about a user's personal audio transcripts. Be conversational and concise.

Today's date: {today}

RELEVANT TRANSCRIPT EXCERPTS (retrieved by semantic search):
The excerpts below may include metadata such as the date they were recorded. Use these dates to answer time-based questions (e.g., "what did I say yesterday?").
{context}

CONVERSATION HISTORY:
{history}

Question: {question}

Answer the question using ONLY the data above. If the data doesn't contain what is asked, say so politely. Do not fabricate information."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> date:
    return date.today()


def _format_history(messages: List[ChatMessage], max_messages: int = 6) -> str:
    recent = messages[-max_messages:]
    lines = []
    for m in recent:
        label = "User" if m.role == "user" else "Assistant"
        lines.append(f"{label}: {m.content}")
    return "\n".join(lines) or "(no previous conversation)"


def _format_context(chunks: List[str]) -> str:
    if not chunks:
        return "(no relevant transcript excerpts found)"
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"[Excerpt {i}]\n{chunk}")
    return "\n\n".join(parts)


# ── Core answer generation ────────────────────────────────────────────────────

def _generate_answer(
    question: str,
    context_chunks: List[str],
    history: List[ChatMessage],
) -> str:
    today = _today().isoformat()
    context_text = _format_context(context_chunks)
    history_text = _format_history(history)

    prompt = ANSWER_PROMPT.format(
        today=today,
        context=context_text,
        history=history_text,
        question=question,
    )

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=2048,
        )
    )
    return response.text.strip()


# ── Public entry point ────────────────────────────────────────────────────────

def ask_question(
    db: Session,
    user_id: UUID,
    question: str,
    session_id: Optional[UUID] = None,
    target_date: Optional[date] = None,
) -> dict:
    # ── Resolve / create session ──────────────────────────────────────────────
    if session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        ).first()
        if not session:
            # Session ID not found — start fresh rather than erroring
            session = ChatSession(user_id=user_id, title=question[:50])
            db.add(session)
            db.commit()
            db.refresh(session)
    else:
        session = ChatSession(user_id=user_id, title=question[:50])
        db.add(session)
        db.commit()
        db.refresh(session)

    # ── Store user message ────────────────────────────────────────────────────
    user_msg = ChatMessage(session_id=session.id, role="user", content=question)
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # ── Load conversation history ─────────────────────────────────────────────
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    # ── RAG: semantic vector search ───────────────────────────────────────────
    try:
        from app.services.embedding_service import vector_search
        context_chunks = vector_search(db, user_id, question, target_date=target_date)
        logger.info(
            "Vector search for session %s returned %d chunks",
            session.id, len(context_chunks),
        )
    except Exception as e:
        logger.warning("Vector search failed, answering without context: %s", e)
        context_chunks = []

    # ── Generate answer ───────────────────────────────────────────────────────
    try:
        answer = _generate_answer(question, context_chunks, history)
    except Exception as e:
        logger.error("Answer generation failed: %s", e)
        answer = "Sorry, I couldn't generate an answer right now. Please try again."

    # ── Store assistant message ───────────────────────────────────────────────
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
