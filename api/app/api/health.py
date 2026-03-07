"""Health and metrics routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import HealthResponse
from app.observability.metrics import metrics_response

router = APIRouter()


@router.get("/health/liveness", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/readiness", response_model=HealthResponse)
def readiness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/metrics")
def metrics():
    return metrics_response()
