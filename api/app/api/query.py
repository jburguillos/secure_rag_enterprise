"""Query API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header

from app.auth.service import resolve_entitlements
from app.models.schemas import QueryRequest, QueryResponse
from app.retrieval.query_service import run_query_flow

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest, authorization: str | None = Header(default=None)) -> QueryResponse:
    transitional_context = request.user_context.model_dump() if request.user_context else None
    entitlements = await resolve_entitlements(
        authorization_header=authorization,
        transitional_context=transitional_context,
    )
    return await run_query_flow(request, entitlements)
