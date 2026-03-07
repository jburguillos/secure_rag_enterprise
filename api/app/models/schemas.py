"""API request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    user_id: str | None = None
    email: str | None = None
    domain: str | None = None
    groups: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    doc_id: str
    doc_name: str | None = None
    page: int | None = None
    chunk_id: str | None = None
    node_id: str
    modality: Literal["text", "image"] = "text"
    webViewLink: str | None = None


class IngestGDriveRequest(BaseModel):
    folder_id: str
    auth_mode: Literal["oauth", "service_account"] = "oauth"
    dry_run: bool = False
    dataset_source: str = "google_drive"


class IngestLocalRequest(BaseModel):
    path: str
    acl_sidecar_path: str
    dry_run: bool = False
    dataset_source: str = "local_folder"


class IngestResponse(BaseModel):
    ingestion_run_id: UUID
    added: int
    updated: int
    deleted: int
    skipped: int
    errors: list[str] = Field(default_factory=list)


class QueryFilters(BaseModel):
    sources: list[str] = Field(default_factory=list)
    mime_types: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    modified_from: datetime | None = None
    modified_to: datetime | None = None


class QueryRequest(BaseModel):
    query: str
    mode: Literal["qa", "summarize"] = "qa"
    top_k: int | None = None
    include_images: bool = True
    filters: QueryFilters | None = None
    user_context: UserContext | None = None


class PolicyDecision(BaseModel):
    decision_id: UUID = Field(default_factory=uuid4)
    allow: bool
    reason: str
    policy_version: str = "1.0"


class QueryResponse(BaseModel):
    run_id: UUID
    answer: str
    refusal_reason: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    policy_decision: PolicyDecision


class FeedbackRequest(BaseModel):
    run_id: UUID
    thumb: Literal["up", "down"]
    reason: str | None = None


class FeedbackResponse(BaseModel):
    feedback_id: UUID
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    detail: str
    metadata: dict[str, Any] = Field(default_factory=dict)
