from datetime import datetime, timezone

from sqlalchemy import delete

from app.database.db import SessionLocal
from app.models.refresh_token import RefreshToken


def clean_expired_revoked_tokens():
    db = SessionLocal()
    try:
        result = db.execute(
            delete(RefreshToken).where(
                RefreshToken.revoked == True,
                RefreshToken.expires_at < datetime.now(timezone.utc),
            )
        )
        db.commit()
        return f"Cleaned up {result.rowcount} tokens"
    finally:
        db.close()
