# Summary generation Celery task
# Runs every 10 minutes (via beat schedule) for ALL users
# Also keeps the original nightly helper for manual runs
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.celery_app import celery_app
from app.database.db import SessionLocal
from app.models.user import User
from app.services.summary_service import create_daily_summary

logger = logging.getLogger(__name__)


@celery_app.task(name="app.jobs.nightly_summary.generate_summaries_for_all_users", bind=True)
def generate_summaries_for_all_users(self):
    """
    Celery task: generate (or refresh) today's summary for every user.

    Scheduled nightly at midnight via Celery Beat so summaries stay current
    as new audio transcripts arrive throughout the day.

    The create_daily_summary() function does an upsert, so repeated calls
    safely update the existing record instead of creating duplicates.
    """
    today = date.today()
    db = SessionLocal()
    success_count = 0
    skip_count = 0
    error_count = 0

    try:
        users = db.execute(select(User)).scalars().all()
        logger.info("[Nightly summary] Processing %d users for %s", len(users), today)

        for user in users:
            try:
                summary = create_daily_summary(db, user.id, today)
                if summary:
                    success_count += 1
                    logger.info("  ✓ [%s] Summary updated", user.email)
                else:
                    skip_count += 1
                    logger.info("  ℹ [%s] No transcripts yet today", user.email)
            except Exception as exc:
                error_count += 1
                logger.error("  ✗ [%s] Failed: %s", user.email, str(exc))

        logger.info(
            "[Nightly summary] Done — %d updated, %d skipped (no transcripts), %d errors",
            success_count,
            skip_count,
            error_count,
        )

    except Exception as exc:
        logger.error("[Nightly summary] Job failed: %s", str(exc))
        raise self.retry(exc=exc, countdown=60, max_retries=3)

    finally:
        db.close()

    return {
        "date": today.isoformat(),
        "success": success_count,
        "skipped": skip_count,
        "errors": error_count,
    }


def generate_nightly_summaries():
    """
    Standalone helper (non-Celery) for manual / script runs.
    Generates yesterday's summary for every user.
    """
    yesterday = date.today() - timedelta(days=1)
    db = SessionLocal()
    try:
        users = db.execute(select(User)).scalars().all()
        for user in users:
            print(f"Generating summary for user {user.email} for {yesterday}")
            try:
                summary = create_daily_summary(db, user.id, yesterday)
                if summary:
                    print("  ✓ Summary created/updated successfully")
                else:
                    print("  ℹ No transcripts found for this date")
            except Exception as e:
                print(f"  ✗ Failed to generate summary: {str(e)}")

        print("Nightly summary generation completed")

    except Exception as e:
        print(f"Nightly summary job failed: {str(e)}")

    finally:
        db.close()


if __name__ == "__main__":
    generate_nightly_summaries()
