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


@router.get("/", status_code=status.HTTP_200_OK)
async def get_summaries(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all summaries for the current user.
    
    Args:
        current_user: Authenticated user (from JWT)
        db: Database session
    
    Returns:
        List of summaries
    """
    from app.models.summary import Summary
    from sqlalchemy import select
    
    summaries = db.execute(
        select(Summary).where(Summary.user_id == current_user.id)
    ).scalars().all()
    
    return {
        "status": "success",
        "summaries": [
            {
                "id": str(s.id),
                "summary_date": s.summary_date.isoformat(),
                "key_discussions": s.key_discussions,
                "decisions": s.decisions,
                "action_items": s.action_items,
                "follow_ups": s.follow_ups,
                "highlights": s.highlights,
                "created_at": s.created_at.isoformat()
            }
            for s in summaries
        ]
    }


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_summary(
    target_date: date = Query(..., description="Date to generate summary for (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generate a daily summary for a specific date.
    
    Args:
        target_date: Date to generate summary for
        current_user: Authenticated user (from JWT)
        db: Database session
    
    Returns:
        Generated summary
    """
    summary = create_daily_summary(db, current_user.id, target_date)
    
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No transcripts found for the specified date"
        )
    
    return {
        "status": "success",
        "message": "Summary generated successfully",
        "summary": {
            "id": str(summary.id),
            "summary_date": summary.summary_date.isoformat(),
            "key_discussions": summary.key_discussions,
            "decisions": summary.decisions,
            "action_items": summary.action_items,
            "follow_ups": summary.follow_ups,
            "highlights": summary.highlights,
            "created_at": summary.created_at.isoformat()
        }
    }


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
