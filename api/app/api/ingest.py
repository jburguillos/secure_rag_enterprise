"""Ingestion API routes."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.db.database import get_session
from app.db.models import IngestionRunRecord
from app.ingestion.pipeline import IngestionService
from app.models.schemas import (
    IngestAcceptedResponse,
    IngestGDriveRequest,
    IngestionRunStatusResponse,
    IngestLocalRequest,
    IngestResponse,
)

router = APIRouter(prefix="/ingest", tags=["ingest"])
logger = logging.getLogger(__name__)


def _run_gdrive_ingest_job(run_id: UUID, request: IngestGDriveRequest) -> None:
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
        logger.exception("Async Google Drive ingestion failed for run_id=%s", run_id)


def _run_local_ingest_job(run_id: UUID, request: IngestLocalRequest) -> None:
    try:
        service = IngestionService()
        service.ingest_local(
            path=request.path,
            acl_sidecar_path=request.acl_sidecar_path,
            dry_run=request.dry_run,
            dataset_source=request.dataset_source,
            run_id=run_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Async local ingestion failed for run_id=%s", run_id)


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


@router.post("/gdrive/async", response_model=IngestAcceptedResponse, status_code=202)
def ingest_gdrive_async(request: IngestGDriveRequest, background_tasks: BackgroundTasks) -> IngestAcceptedResponse:
    run_id = IngestionService.start_ingestion_run(
        source="google_drive",
        dataset_source=request.dataset_source,
        metadata={"folder_id": request.folder_id, "auth_mode": request.auth_mode, "async": True},
    )
    background_tasks.add_task(_run_gdrive_ingest_job, run_id, request)
    return IngestAcceptedResponse(ingestion_run_id=run_id, status="running")


@router.post("/local/async", response_model=IngestAcceptedResponse, status_code=202)
def ingest_local_async(request: IngestLocalRequest, background_tasks: BackgroundTasks) -> IngestAcceptedResponse:
    run_id = IngestionService.start_ingestion_run(
        source="local_folder",
        dataset_source=request.dataset_source,
        metadata={"path": request.path, "acl_sidecar_path": request.acl_sidecar_path, "async": True},
    )
    background_tasks.add_task(_run_local_ingest_job, run_id, request)
    return IngestAcceptedResponse(ingestion_run_id=run_id, status="running")


@router.get("/runs/{run_id}", response_model=IngestionRunStatusResponse)
def get_ingestion_run_status(run_id: str) -> IngestionRunStatusResponse:
    try:
        run_uuid = UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid ingestion_run_id format") from exc

    with get_session() as session:
        run = session.get(IngestionRunRecord, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail="ingestion_run_id not found")

        metadata = dict(run.meta_json or {})
        payload = {
            "ingestion_run_id": run.ingestion_run_id,
            "source": run.source,
            "dataset_source": run.dataset_source,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "status": run.status,
            "added": run.added_count,
            "updated": run.updated_count,
            "deleted": run.deleted_count,
            "skipped": run.skipped_count,
            "errors": [str(err) for err in (run.errors or [])],
        }

    return IngestionRunStatusResponse(
        ingestion_run_id=payload["ingestion_run_id"],
        source=payload["source"],
        dataset_source=payload["dataset_source"],
        started_at=payload["started_at"],
        ended_at=payload["ended_at"],
        status=payload["status"],
        added=payload["added"],
        updated=payload["updated"],
        deleted=payload["deleted"],
        skipped=payload["skipped"],
        text_nodes_indexed=int(metadata.get("text_nodes_indexed") or 0),
        image_nodes_indexed=int(metadata.get("image_nodes_indexed") or 0),
        errors=payload["errors"],
        metadata=metadata,
    )
