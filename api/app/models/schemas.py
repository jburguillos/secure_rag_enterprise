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
    sheet_name: str | None = None
    cell_range: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    tabular_node_type: str | None = None
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
    text_nodes_indexed: int = 0
    image_nodes_indexed: int = 0
    errors: list[str] = Field(default_factory=list)


class IngestAcceptedResponse(BaseModel):
    ingestion_run_id: UUID
    status: Literal["running"] = "running"


class IngestionRunStatusResponse(BaseModel):
    ingestion_run_id: UUID
    source: str
    dataset_source: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str
    added: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    text_nodes_indexed: int = 0
    image_nodes_indexed: int = 0
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryFilters(BaseModel):
    sources: list[str] = Field(default_factory=list)
    mime_types: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    folder_prefixes: list[str] = Field(default_factory=list)
    path_prefixes: list[str] = Field(default_factory=list)
    modified_from: datetime | None = None
    modified_to: datetime | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class GenerationOverrides(BaseModel):
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class QueryRequest(BaseModel):
    query: str
    mode: Literal["qa", "summarize"] = "qa"
    retrieval_mode: Literal["auto", "rag", "chat"] = "auto"
    top_k: int | None = None
    include_images: bool = True
    filters: QueryFilters | None = None
    chat_history: list[ChatMessage] = Field(default_factory=list)
    user_context: UserContext | None = None
    generation_overrides: GenerationOverrides | None = None


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


class AdminDriveGroupMapRequest(BaseModel):
    mapping: dict[str, list[str]] = Field(default_factory=dict)


class AdminDriveGroupMapResponse(BaseModel):
    mapping: dict[str, list[str]] = Field(default_factory=dict)
    source: Literal["env", "db"] = "env"


class AdminAccessPreviewRequest(BaseModel):
    principal: UserContext
    sources: list[str] = Field(default_factory=list)
    limit: int = 100


class AdminPreviewDocument(BaseModel):
    doc_id: str
    source: str
    title: str | None = None
    mime_type: str | None = None
    modified_time: datetime | None = None
    permissions_summary: dict[str, Any] = Field(default_factory=dict)


class AdminAccessPreviewResponse(BaseModel):
    principal_groups: list[str] = Field(default_factory=list)
    total_scanned: int
    allowed_count: int
    documents: list[AdminPreviewDocument] = Field(default_factory=list)


class AdminGroup(BaseModel):
    id: str
    name: str
    path: str | None = None


class AdminGroupListResponse(BaseModel):
    groups: list[AdminGroup] = Field(default_factory=list)


class AdminUser(BaseModel):
    id: str
    username: str
    email: str | None = None
    enabled: bool = True
    first_name: str | None = None
    last_name: str | None = None


class AdminUserListResponse(BaseModel):
    users: list[AdminUser] = Field(default_factory=list)


class AdminCreateUserRequest(BaseModel):
    username: str
    email: str
    password: str
    groups: list[str] = Field(default_factory=list)
    first_name: str | None = None
    last_name: str | None = None
    enabled: bool = True


class AdminCreateUserResponse(BaseModel):
    user_id: str
    username: str
    groups: list[str] = Field(default_factory=list)


class AdminSetUserGroupsRequest(BaseModel):
    groups: list[str] = Field(default_factory=list)


class AdminUserGroupsResponse(BaseModel):
    user_id: str
    groups: list[str] = Field(default_factory=list)
