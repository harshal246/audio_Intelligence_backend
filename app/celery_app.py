from celery import Celery
from app.config import settings

celery_app = Celery(
    "audio_intelligence",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.jobs.embeddings",
    ],
)

celery_app.conf.beat_schedule = {}

celery_app.conf.timezone = "UTC"
