from celery import Celery
from celery.schedules import crontab

celery_app = Celery(
    "audio_intelligence",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
    include=[
        "app.jobs.cleanup",
        "app.jobs.nightly_summary",
    ],
)

celery_app.conf.beat_schedule = {
    # Existing: remove expired JWT tokens every 5 minutes
    "cleanup-expired-tokens-every-5-min": {
        "task": "app.jobs.cleanup.clean_expired_revoked_tokens",
        "schedule": crontab(minute="*/5"),
    },

    # New: generate summaries for ALL users nightly at midnight (12:00 AM)
    "generate-all-user-summaries-nightly": {
        "task": "app.jobs.nightly_summary.generate_summaries_for_all_users",
        "schedule": crontab(hour=0, minute=0),  # Midnight UTC
    },
}

celery_app.conf.timezone = "UTC"
