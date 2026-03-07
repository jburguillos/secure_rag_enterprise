"""Feedback API routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.db.database import get_session
from app.db.repository import insert_feedback
from app.models.schemas import FeedbackRequest, FeedbackResponse

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackResponse)
def feedback(request: FeedbackRequest) -> FeedbackResponse:
    with get_session() as session:
        feedback_id = insert_feedback(session, run_id=request.run_id, thumb=request.thumb, reason=request.reason)
    return FeedbackResponse(feedback_id=feedback_id, created_at=datetime.now(timezone.utc))
