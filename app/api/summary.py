# Summary API endpoints - handles AI-powered summaries
from datetime import date
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.services.summary_service import create_daily_summary, get_daily_transcripts, create_custom_summary
from app.utils.auth import get_current_user

router = APIRouter(prefix="/summary", tags=["summary"])

class CustomSummaryRequest(BaseModel):
    transcript_ids: List[UUID]

@router.post("/preview", status_code=status.HTTP_201_CREATED)
async def preview_summary(
    target_date: date = Query(..., description="Date to preview summary for (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generate a plain-text summary for a specific date and SAVE it to the database.
    Uses the structured analyst prompt (TITLE + sections).
    If a summary for the same date already exists, it is updated in-place.

    Returns:
        summary_id : ID of the saved Summary record
        title      : concise 5-word title
        summary    : full formatted plain-text summary
    """
    transcripts = get_daily_transcripts(db, current_user.id, target_date)

    if not transcripts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No transcripts found for the specified date"
        )

    summary = create_daily_summary(db, current_user.id, target_date)

    return {
        "status": "success",
        "summary_id": str(summary.id),
        "date": target_date.isoformat(),
        "title": summary.title,
        "summary": summary.summary_text,
    }


@router.post("/custom", status_code=status.HTTP_201_CREATED)
async def custom_summary(
    body: CustomSummaryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate and SAVE a plain-text summary for a specific list of transcript IDs.
    Uses the same analyst prompt as /preview.

    Body (JSON):
        transcript_ids: list of transcript UUID strings

    Returns:
        summary_id     : ID of the saved Summary record
        title          : concise title
        summary_text   : full formatted plain-text summary
        transcript_ids : the IDs that were used
    """
    summary = create_custom_summary(db, current_user.id, body.transcript_ids)

    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No transcripts found for the provided IDs.",
        )

    return {
        "status": "success",
        "summary_id": str(summary.id),
        "title": summary.title,
        "summary_text": summary.summary_text,
        "transcript_ids": [str(tid) for tid in (summary.transcript_ids or [])],
    }
