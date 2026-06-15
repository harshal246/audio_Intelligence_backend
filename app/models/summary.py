# Summary model stores AI-generated daily summaries
import uuid

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.db import Base


class Summary(Base):
    __tablename__ = "summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    summary_date = Column(DateTime(timezone=True), nullable=False)
    key_discussions = Column(Text)
    decisions = Column(Text)
    action_items = Column(Text)
    follow_ups = Column(Text)
    highlights = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship to User model
    user = relationship("User", backref="summaries")
