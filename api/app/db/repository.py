"""Persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import (
    DocumentRecord,
    FeedbackEventRecord,
    IngestionRunRecord,
    PolicyDecisionRecord,
    QueryCitationRecord,
    QueryEvidenceRecord,
    QueryRunRecord,
)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def upsert_document(
    session: Session,
    *,
    doc_id: str,
    source: str,
    title: str | None,
    mime_type: str | None,
    modified_time: datetime | None,
    content_hash: str,
    permissions_summary: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    rec = session.get(DocumentRecord, doc_id)
    if rec is None:
        rec = DocumentRecord(
            doc_id=doc_id,
            source=source,
            title=title,
            mime_type=mime_type,
            modified_time=modified_time,
            content_hash=content_hash,
            permissions_summary=_json_safe(permissions_summary),
            meta_json=_json_safe(metadata),
        )
        session.add(rec)
        return

    rec.source = source
    rec.title = title
    rec.mime_type = mime_type
    rec.modified_time = modified_time
    rec.content_hash = content_hash
    rec.permissions_summary = _json_safe(permissions_summary)
    rec.meta_json = _json_safe(metadata)


def create_ingestion_run(session: Session, *, source: str, dataset_source: str, metadata: dict[str, Any] | None = None) -> UUID:
    run_id = uuid4()
    rec = IngestionRunRecord(
        ingestion_run_id=run_id,
        source=source,
        dataset_source=dataset_source,
        started_at=datetime.now(timezone.utc),
        status="running",
        meta_json=_json_safe(metadata or {}),
    )
    session.add(rec)
    return run_id


def finalize_ingestion_run(session: Session, *, run_id: UUID, added: int, updated: int, deleted: int, skipped: int, errors: list[str]) -> None:
    rec = session.get(IngestionRunRecord, run_id)
    if rec is None:
        return
    rec.ended_at = datetime.now(timezone.utc)
    rec.status = "completed" if not errors else "completed_with_errors"
    rec.added_count = added
    rec.updated_count = updated
    rec.deleted_count = deleted
    rec.skipped_count = skipped
    rec.error_count = len(errors)
    rec.errors = errors


def insert_policy_decision(
    session: Session,
    *,
    decision_id: UUID,
    run_id: UUID | None,
    user_id_hash: str | None,
    user_groups: list[str],
    policy_input: dict[str, Any],
    policy_result: dict[str, Any],
    policy_version: str | None,
) -> None:
    session.add(
        PolicyDecisionRecord(
            decision_id=decision_id,
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            user_id_hash=user_id_hash,
            user_groups=_json_safe(user_groups),
            policy_input=_json_safe(policy_input),
            policy_result=_json_safe(policy_result),
            policy_version=policy_version,
        )
    )


def insert_query_run(
    session: Session,
    *,
    run_id: UUID,
    user_id_hash: str | None,
    user_groups: list[str],
    query_hash: str,
    raw_query: str | None,
    mode: str,
    response_status: str,
    refusal_reason: str | None,
    model_id: str | None,
    model_version: str | None,
    config_version: str | None,
    policy_decision_id: UUID | None,
) -> None:
    session.add(
        QueryRunRecord(
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            user_id_hash=user_id_hash,
            user_groups=_json_safe(user_groups),
            query_hash=query_hash,
            raw_query=raw_query,
            mode=mode,
            response_status=response_status,
            refusal_reason=refusal_reason,
            model_id=model_id,
            model_version=model_version,
            config_version=config_version,
            policy_decision_id=policy_decision_id,
        )
    )


def insert_evidence_rows(session: Session, *, run_id: UUID, evidence_rows: list[dict[str, Any]]) -> None:
    for row in evidence_rows:
        session.add(
            QueryEvidenceRecord(
                id=uuid4(),
                run_id=run_id,
                node_id=str(row.get("node_id", "")),
                doc_id=row.get("doc_id"),
                page=row.get("page"),
                chunk_id=row.get("chunk_id"),
                modality=row.get("modality"),
                score=row.get("score"),
                payload=_json_safe(row),
            )
        )


def insert_citation_rows(session: Session, *, run_id: UUID, citation_rows: list[dict[str, Any]]) -> None:
    for row in citation_rows:
        session.add(
            QueryCitationRecord(
                id=uuid4(),
                run_id=run_id,
                node_id=str(row.get("node_id", "")),
                doc_id=row.get("doc_id"),
                page=row.get("page"),
                chunk_id=row.get("chunk_id"),
                modality=row.get("modality"),
                citation_label=row.get("doc_name"),
            )
        )


def insert_feedback(session: Session, *, run_id: UUID, thumb: str, reason: str | None) -> UUID:
    feedback_id = uuid4()
    session.add(
        FeedbackEventRecord(
            id=feedback_id,
            run_id=run_id,
            thumb=thumb,
            reason=reason,
        )
    )
    return feedback_id
