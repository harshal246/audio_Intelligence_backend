from datetime import datetime, timezone

from sqlalchemy import delete, or_

from app.database.db import SessionLocal
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.celery_app import celery_app


@celery_app.task(name="app.jobs.cleanup.clean_expired_revoked_tokens")
def clean_expired_revoked_tokens():
    db = SessionLocal()
    try:
        # Clean up expired or revoked refresh tokens
        result = db.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.revoked == True,
                    RefreshToken.expires_at < datetime.now(timezone.utc),
                )
            )
        )

        # Clean up expired or already-used password reset tokens
        reset_result = db.execute(
            delete(PasswordResetToken).where(
                or_(
                    PasswordResetToken.used == True,
                    PasswordResetToken.expires_at < datetime.now(timezone.utc),
                )
            )
        )

        db.commit()
        return (
            f"Cleaned up {result.rowcount} refresh tokens, "
            f"{reset_result.rowcount} reset tokens"
        )
    finally:
        db.close()
