from datetime import datetime, timezone

from sqlalchemy import delete, or_

from app.database.db import SessionLocal
from app.models.refresh_token import RefreshToken
from app.celery_app import celery_app


@celery_app.task(name="app.jobs.cleanup.clean_expired_revoked_tokens")
def clean_expired_revoked_tokens():
    db = SessionLocal()
    try:
        result = db.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.revoked == True,
                    RefreshToken.expires_at < datetime.now(timezone.utc),
                )
            )
        )
        db.commit()
        return f"Cleaned up {result.rowcount} tokens"
    finally:
        db.close()
