import logging
import uuid

from app.celery_app import celery_app
from app.database.db import SessionLocal
from app.models.transcript import Transcript

logger = logging.getLogger(__name__)


@celery_app.task(name="app.jobs.embeddings.generate_embeddings", bind=True, max_retries=3)
def generate_embeddings(self, transcript_id: str):
    logger.info("Starting embedding generation for transcript %s", transcript_id)
    db = SessionLocal()
    try:
        t_uuid = uuid.UUID(transcript_id)
        transcript = db.query(Transcript).filter(Transcript.id == t_uuid).first()
        if not transcript:
            logger.error("Transcript %s not found", transcript_id)
            return

        from app.services.transcript_service import _trigger_embeddings
        _trigger_embeddings(db, transcript)
        logger.info("Embeddings completed for transcript %s", transcript_id)
    except Exception as exc:
        logger.exception("Embedding generation failed for transcript %s", transcript_id)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
