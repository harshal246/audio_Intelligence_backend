from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str
    session_id: Optional[UUID] = None
    target_date: Optional[date] = None


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class AskResponse(BaseModel):
    session_id: UUID
    answer: str
    history: List[MessageOut]


class SessionOut(BaseModel):
    id: UUID
    title: str
    message_count: int
    created_at: datetime
    last_message_at: Optional[datetime] = None
    history: List[MessageOut] = []
