from celery import Celery
from celery.schedules import crontab

celery_app = Celery(
    "audio_intelligence",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)

celery_app.conf.beat_schedule = {
    "cleanup-expired-tokens-every-hour": {
        "task": "app.jobs.cleanup.clean_expired_revoked_tokens",
        "schedule": crontab(minute=0),
    },
}

celery_app.conf.timezone = "UTC"
