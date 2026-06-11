# RefreshToken model stores refresh tokens so they can be revoked individually.
# We store a SHA-256 hash of the token, never the raw token,
# so even if the DB is leaked, tokens can't be reused.
import uuid

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.db import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    # Links to the user who owns this token — CASCADE deletes tokens if user is deleted
    # UUID type matches the User.id column
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # SHA-256 hash of the raw JWT — unique to prevent duplicate storage
    token_hash = Column(String, unique=True, nullable=False)
    # When this token expires — unused expired tokens are cleaned up later
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Soft-delete flag — set to True when token is used for refresh
    revoked = Column(Boolean, default=False)
    # When this record was created
    created_at = Column(DateTime(timezone=True), server_default=func.now())
