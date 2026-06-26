# PasswordResetToken stores one-time tokens for the "Forgot Password" flow.
# We store a SHA-256 hash of the raw token (never the plaintext)
# so even if the DB is compromised, tokens cannot be reused by an attacker.
import uuid

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.db import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    # Which user requested the reset — CASCADE deletes rows if user is deleted
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # SHA-256 hash of the raw token URL param — stored hashed for security
    token_hash = Column(String, unique=True, nullable=False)
    # Short expiry window (15 min) — reduces exposure window if token is intercepted
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Marks token as consumed after first use — prevents replay attacks
    used = Column(Boolean, default=False)
    # Audit trail — helps debug support tickets
    created_at = Column(DateTime(timezone=True), server_default=func.now())
