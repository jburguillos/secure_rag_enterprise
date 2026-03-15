from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.database import get_session
from app.db.models import IngestionRunRecord
from app.main import app


def test_local_async_ingest_starts_and_status_can_be_polled(monkeypatch) -> None:
    monkeypatch.setattr("app.api.ingest._run_local_ingest_job", lambda run_id, request: None)

    with TestClient(app) as client:
        response = client.post(
            "/ingest/local/async",
            json={
                "path": "./tests/data/sample_docs",
                "acl_sidecar_path": "./tests/data/sample_docs/acl_map.yaml",
                "dry_run": False,
                "dataset_source": "local_folder",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "running"
        run_id = body["ingestion_run_id"]

        status_response = client.get(f"/ingest/runs/{run_id}")
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["ingestion_run_id"] == run_id
        assert status_body["status"] == "running"
        assert status_body["source"] == "local_folder"


def test_ingest_run_status_includes_index_counts_from_metadata() -> None:
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.add(
            IngestionRunRecord(
                ingestion_run_id=run_id,
                source="google_drive",
                dataset_source="google_drive",
                started_at=now,
                ended_at=now,
                status="completed",
                added_count=4,
                skipped_count=1,
                error_count=0,
                errors=[],
                meta_json={"text_nodes_indexed": 60, "image_nodes_indexed": 114, "folder_id": "root-folder"},
            )
        )

    with TestClient(app) as client:
        response = client.get(f"/ingest/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["text_nodes_indexed"] == 60
    assert payload["image_nodes_indexed"] == 114
    assert payload["metadata"]["folder_id"] == "root-folder"
