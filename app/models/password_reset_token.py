# PasswordResetToken stores OTP codes for the "Forgot Password" flow.
# We store SHA-256 hashes of both the OTP and the post-verification session token.
# Raw values are never persisted — only hashes are stored for security.
import uuid

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.db import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    # Which user requested the reset — CASCADE deletes rows if user is deleted
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # SHA-256 hash of the 6-digit OTP — never store the raw OTP
    otp_hash = Column(String, unique=True, nullable=False)
    # SHA-256 hash of the short-lived reset session token issued after OTP verification
    # Null until OTP is verified
    reset_session_hash = Column(String, unique=True, nullable=True)
    # Short expiry window (15 min) — reduces exposure window
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # True once OTP has been verified — unlocks password reset step
    otp_verified = Column(Boolean, default=False)
    # True once the password has been reset — prevents reuse
    used = Column(Boolean, default=False)
    # Audit trail — helps debug support tickets
    created_at = Column(DateTime(timezone=True), server_default=func.now())
