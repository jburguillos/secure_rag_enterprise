"""Ingestion API routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.ingestion.pipeline import IngestionService
from app.models.schemas import IngestGDriveRequest, IngestLocalRequest, IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/gdrive", response_model=IngestResponse)
def ingest_gdrive(request: IngestGDriveRequest) -> IngestResponse:
    service = IngestionService()
    return service.ingest_gdrive(
        folder_id=request.folder_id,
        auth_mode=request.auth_mode,
        dry_run=request.dry_run,
        dataset_source=request.dataset_source,
    )


@router.post("/local", response_model=IngestResponse)
def ingest_local(request: IngestLocalRequest) -> IngestResponse:
    service = IngestionService()
    return service.ingest_local(
        path=request.path,
        acl_sidecar_path=request.acl_sidecar_path,
        dry_run=request.dry_run,
        dataset_source=request.dataset_source,
    )
