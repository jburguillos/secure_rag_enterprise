"""Admin API routes for access operations and identity management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select

from app.admin.authz import require_admin_entitlements
from app.admin.keycloak_admin_client import KeycloakAdminClient, KeycloakAdminError
from app.admin.settings_store import read_drive_group_map, save_drive_group_map
from app.auth.context import Entitlements
from app.auth.group_mapping import apply_drive_group_mapping
from app.db.database import get_session
from app.db.models import DocumentRecord
from app.ingestion.pipeline import IngestionService
from app.models.schemas import (
    AdminAccessPreviewRequest,
    AdminAccessPreviewResponse,
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminDriveGroupMapRequest,
    AdminDriveGroupMapResponse,
    AdminGroup,
    AdminGroupListResponse,
    AdminPreviewDocument,
    AdminSetUserGroupsRequest,
    AdminUser,
    AdminUserGroupsResponse,
    AdminUserListResponse,
    IngestAcceptedResponse,
    IngestGDriveRequest,
    IngestResponse,
)
from app.retrieval.acl import payload_access_allowed

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


def _run_admin_gdrive_ingest_job(run_id, request: IngestGDriveRequest) -> None:
    try:
        service = IngestionService()
        service.ingest_gdrive(
            folder_id=request.folder_id,
            auth_mode=request.auth_mode,
            dry_run=request.dry_run,
            dataset_source=request.dataset_source,
            run_id=run_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Async admin Google Drive sync failed for run_id=%s", run_id)


@router.get("/settings/drive-group-map", response_model=AdminDriveGroupMapResponse)
def get_drive_group_map(_admin: Entitlements = Depends(require_admin_entitlements)) -> AdminDriveGroupMapResponse:
    mapping, source = read_drive_group_map()
    return AdminDriveGroupMapResponse(mapping=mapping, source=source)


@router.put("/settings/drive-group-map", response_model=AdminDriveGroupMapResponse)
def set_drive_group_map(
    request: AdminDriveGroupMapRequest,
    _admin: Entitlements = Depends(require_admin_entitlements),
) -> AdminDriveGroupMapResponse:
    mapping = save_drive_group_map(request.mapping)
    return AdminDriveGroupMapResponse(mapping=mapping, source="db")


@router.post("/access/preview", response_model=AdminAccessPreviewResponse)
def preview_access(
    request: AdminAccessPreviewRequest,
    _admin: Entitlements = Depends(require_admin_entitlements),
) -> AdminAccessPreviewResponse:
    principal = Entitlements.from_transitional(request.principal.model_dump())
    principal = apply_drive_group_mapping(principal)

    limit = max(1, min(int(request.limit), 500))
    with get_session() as session:
        stmt = select(DocumentRecord).order_by(DocumentRecord.updated_at.desc())
        if request.sources:
            stmt = stmt.where(DocumentRecord.source.in_(request.sources))
        docs = list(session.execute(stmt.limit(limit)).scalars().all())

        allowed_docs: list[AdminPreviewDocument] = []
        for doc in docs:
            acl = doc.permissions_summary if isinstance(doc.permissions_summary, dict) else {}
            if payload_access_allowed(acl, principal):
                allowed_docs.append(
                    AdminPreviewDocument(
                        doc_id=doc.doc_id,
                        source=doc.source,
                        title=doc.title,
                        mime_type=doc.mime_type,
                        modified_time=doc.modified_time,
                        permissions_summary=acl,
                    )
                )

    return AdminAccessPreviewResponse(
        principal_groups=principal.groups,
        total_scanned=len(docs),
        allowed_count=len(allowed_docs),
        documents=allowed_docs,
    )


@router.get("/keycloak/groups", response_model=AdminGroupListResponse)
async def keycloak_groups(_admin: Entitlements = Depends(require_admin_entitlements)) -> AdminGroupListResponse:
    client = KeycloakAdminClient()
    try:
        groups = await client.list_groups()
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AdminGroupListResponse(groups=[AdminGroup(id=item.id, name=item.name, path=item.path) for item in groups])


@router.get("/keycloak/users", response_model=AdminUserListResponse)
async def keycloak_users(
    search: str | None = Query(default=None),
    max_users: int = Query(default=100, alias="max"),
    _admin: Entitlements = Depends(require_admin_entitlements),
) -> AdminUserListResponse:
    client = KeycloakAdminClient()
    try:
        users = await client.list_users(search=search, max_users=max_users)
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AdminUserListResponse(
        users=[
            AdminUser(
                id=item.id,
                username=item.username,
                email=item.email,
                enabled=item.enabled,
                first_name=item.first_name,
                last_name=item.last_name,
            )
            for item in users
        ]
    )


@router.post("/keycloak/users", response_model=AdminCreateUserResponse)
async def keycloak_create_user(
    request: AdminCreateUserRequest,
    _admin: Entitlements = Depends(require_admin_entitlements),
) -> AdminCreateUserResponse:
    client = KeycloakAdminClient()
    try:
        user_id, final_groups = await client.create_user(
            username=request.username,
            email=request.email,
            password=request.password,
            groups=request.groups,
            first_name=request.first_name,
            last_name=request.last_name,
            enabled=request.enabled,
        )
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AdminCreateUserResponse(user_id=user_id, username=request.username, groups=final_groups)


@router.put("/keycloak/users/{user_id}/groups", response_model=AdminUserGroupsResponse)
async def keycloak_set_user_groups(
    user_id: str,
    request: AdminSetUserGroupsRequest,
    _admin: Entitlements = Depends(require_admin_entitlements),
) -> AdminUserGroupsResponse:
    client = KeycloakAdminClient()
    try:
        final_groups = await client.set_user_groups(user_id=user_id, groups=request.groups)
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AdminUserGroupsResponse(user_id=user_id, groups=final_groups)


@router.post("/sync/gdrive", response_model=IngestResponse)
def sync_gdrive(
    request: IngestGDriveRequest,
    _admin: Entitlements = Depends(require_admin_entitlements),
) -> IngestResponse:
    service = IngestionService()
    return service.ingest_gdrive(
        folder_id=request.folder_id,
        auth_mode=request.auth_mode,
        dry_run=request.dry_run,
        dataset_source=request.dataset_source,
    )


@router.post("/sync/gdrive/async", response_model=IngestAcceptedResponse, status_code=202)
def sync_gdrive_async(
    request: IngestGDriveRequest,
    background_tasks: BackgroundTasks,
    _admin: Entitlements = Depends(require_admin_entitlements),
) -> IngestAcceptedResponse:
    run_id = IngestionService.start_ingestion_run(
        source="google_drive",
        dataset_source=request.dataset_source,
        metadata={"folder_id": request.folder_id, "auth_mode": request.auth_mode, "async": True, "admin_sync": True},
    )
    background_tasks.add_task(_run_admin_gdrive_ingest_job, run_id, request)
    return IngestAcceptedResponse(ingestion_run_id=run_id, status="running")
