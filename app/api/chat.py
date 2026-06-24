from datetime import date, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.chat import ChatSession, ChatMessage
from app.models.user import User
from app.schemas.chat import AskRequest, AskResponse, SessionOut, MessageOut
from app.services.chat_service import ask_question
from app.utils.auth import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/ask", response_model=AskResponse, status_code=status.HTTP_200_OK)
async def ask(
    body: AskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Ask a natural-language question about your transcripts and summaries.

    - **question**: Your question (e.g. "what did I do on the 17th?")
    - **session_id** (optional): Pass to continue an existing conversation.
      Omit to start a new chat session.

    Returns the answer along with the full message history for the session.
    """
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty",
        )

    result = ask_question(db, current_user.id, body.question.strip(), body.session_id, body.target_date)

    return AskResponse(
        session_id=result["session_id"],
        answer=result["answer"],
        history=result["history"],
    )


@router.get("/sessions", response_model=List[SessionOut])
async def list_sessions(
    target_date: date | None = Query(None, description="Filter sessions by date (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List chat sessions for the current user.

    - **target_date** (optional): If provided, only sessions with activity on this date are returned.
    """
    q = db.query(ChatSession).filter(ChatSession.user_id == current_user.id)

    if target_date:
        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date, datetime.max.time())
        q = q.filter(ChatSession.created_at >= start, ChatSession.created_at <= end)

    sessions = q.order_by(ChatSession.created_at.desc()).all()

    result = []
    for s in sessions:
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == s.id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        msg_count = len(messages)
        last_msg = messages[-1] if messages else None
        
        history = [
            MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)
            for m in messages
        ]

        result.append(SessionOut(
            id=s.id,
            title=s.title or "Chat",
            message_count=msg_count,
            created_at=s.created_at,
            last_message_at=last_msg.created_at if last_msg else None,
            history=history,
        ))

    return result
