# Transcript model stores processed audio transcription data
import uuid

from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.db import Base


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    audio_filename = Column(String, nullable=False)
    full_transcript_data = Column(JSON, nullable=False)
    processing_timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship to User model
    user = relationship("User", backref="transcripts")
