# Summary API endpoints - handles AI-powered summaries
from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.services.summary_service import create_daily_summary, get_daily_transcripts, generate_preview_summary
from app.utils.auth import get_current_user

router = APIRouter(prefix="/summary", tags=["summary"])



@router.post("/preview", status_code=status.HTTP_200_OK)
async def preview_summary(
    target_date: date = Query(..., description="Date to preview summary for (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generate a formatted plain-text summary for a specific date.
    Uses the structured analyst prompt (TITLE + sections).
    Nothing is stored in the database.

    Returns:
        title   : concise 5-word title
        summary : full formatted plain-text summary
    """
    transcripts = get_daily_transcripts(db, current_user.id, target_date)

    if not transcripts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No transcripts found for the specified date"
        )

    result = generate_preview_summary(transcripts)

    return {
        "status": "success",
        "date": target_date.isoformat(),
        "title": result["title"],
        "summary": result["summary"]
    }
