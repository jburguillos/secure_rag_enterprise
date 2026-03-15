"""Persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import (
    AdminSettingRecord,
    DocumentRecord,
    FeedbackEventRecord,
    IngestionRunRecord,
    PolicyDecisionRecord,
    QueryCitationRecord,
    QueryEvidenceRecord,
    QueryRunRecord,
)


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\x00", "")


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {_sanitize_value(key): _sanitize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    if isinstance(value, set):
        return [_sanitize_value(item) for item in value]
    return value


def _json_safe(value: Any) -> Any:
    sanitized = _sanitize_value(value)
    return json.loads(json.dumps(sanitized, default=str))


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
    sanitized_doc_id = _sanitize_text(doc_id) or ""
    sanitized_source = _sanitize_text(source) or ""
    sanitized_title = _sanitize_text(title)
    sanitized_mime_type = _sanitize_text(mime_type)
    sanitized_content_hash = _sanitize_text(content_hash)

    rec = session.get(DocumentRecord, sanitized_doc_id)
    if rec is None:
        rec = DocumentRecord(
            doc_id=sanitized_doc_id,
            source=sanitized_source,
            title=sanitized_title,
            mime_type=sanitized_mime_type,
            modified_time=modified_time,
            content_hash=sanitized_content_hash,
            permissions_summary=_json_safe(permissions_summary),
            meta_json=_json_safe(metadata),
        )
        session.add(rec)
        return

    rec.source = sanitized_source
    rec.title = sanitized_title
    rec.mime_type = sanitized_mime_type
    rec.modified_time = modified_time
    rec.content_hash = sanitized_content_hash
    rec.permissions_summary = _json_safe(permissions_summary)
    rec.meta_json = _json_safe(metadata)


def create_ingestion_run(session: Session, *, source: str, dataset_source: str, metadata: dict[str, Any] | None = None) -> UUID:
    run_id = uuid4()
    rec = IngestionRunRecord(
        ingestion_run_id=run_id,
        source=_sanitize_text(source) or "",
        dataset_source=_sanitize_text(dataset_source) or "",
        started_at=datetime.now(timezone.utc),
        status="running",
        meta_json=_json_safe(metadata or {}),
    )
    session.add(rec)
    return run_id


def update_ingestion_run(
    session: Session,
    *,
    run_id: UUID,
    status: str | None = None,
    ended_at: datetime | None = None,
    added: int | None = None,
    updated: int | None = None,
    deleted: int | None = None,
    skipped: int | None = None,
    errors: list[str] | None = None,
    metadata_updates: dict[str, Any] | None = None,
) -> None:
    rec = session.get(IngestionRunRecord, run_id)
    if rec is None:
        return
    if ended_at is not None:
        rec.ended_at = ended_at
    if status is not None:
        rec.status = _sanitize_text(status) or ""
    if added is not None:
        rec.added_count = added
    if updated is not None:
        rec.updated_count = updated
    if deleted is not None:
        rec.deleted_count = deleted
    if skipped is not None:
        rec.skipped_count = skipped
    if errors is not None:
        rec.error_count = len(errors)
        rec.errors = _json_safe(errors)
    if metadata_updates:
        meta = dict(rec.meta_json or {})
        meta.update(_json_safe(metadata_updates))
        rec.meta_json = meta


def finalize_ingestion_run(
    session: Session,
    *,
    run_id: UUID,
    added: int,
    updated: int,
    deleted: int,
    skipped: int,
    errors: list[str],
    metadata_updates: dict[str, Any] | None = None,
) -> None:
    update_ingestion_run(
        session,
        run_id=run_id,
        ended_at=datetime.now(timezone.utc),
        status="completed" if not errors else "completed_with_errors",
        added=added,
        updated=updated,
        deleted=deleted,
        skipped=skipped,
        errors=errors,
        metadata_updates=metadata_updates,
    )


def fail_ingestion_run(
    session: Session,
    *,
    run_id: UUID,
    errors: list[str],
    metadata_updates: dict[str, Any] | None = None,
) -> None:
    update_ingestion_run(
        session,
        run_id=run_id,
        ended_at=datetime.now(timezone.utc),
        status="failed",
        errors=errors,
        metadata_updates=metadata_updates,
    )


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
            user_id_hash=_sanitize_text(user_id_hash),
            user_groups=_json_safe(user_groups),
            policy_input=_json_safe(policy_input),
            policy_result=_json_safe(policy_result),
            policy_version=_sanitize_text(policy_version),
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
            user_id_hash=_sanitize_text(user_id_hash),
            user_groups=_json_safe(user_groups),
            query_hash=_sanitize_text(query_hash) or "",
            raw_query=_sanitize_text(raw_query),
            mode=_sanitize_text(mode) or "",
            response_status=_sanitize_text(response_status) or "",
            refusal_reason=_sanitize_text(refusal_reason),
            model_id=_sanitize_text(model_id),
            model_version=_sanitize_text(model_version),
            config_version=_sanitize_text(config_version),
            policy_decision_id=policy_decision_id,
        )
    )


def insert_evidence_rows(session: Session, *, run_id: UUID, evidence_rows: list[dict[str, Any]]) -> None:
    for row in evidence_rows:
        session.add(
            QueryEvidenceRecord(
                id=uuid4(),
                run_id=run_id,
                node_id=_sanitize_text(str(row.get("node_id", ""))) or "",
                doc_id=_sanitize_text(row.get("doc_id")),
                page=row.get("page"),
                chunk_id=_sanitize_text(row.get("chunk_id")),
                modality=_sanitize_text(row.get("modality")),
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
                node_id=_sanitize_text(str(row.get("node_id", ""))) or "",
                doc_id=_sanitize_text(row.get("doc_id")),
                page=row.get("page"),
                chunk_id=_sanitize_text(row.get("chunk_id")),
                modality=_sanitize_text(row.get("modality")),
                citation_label=_sanitize_text(row.get("doc_name")),
            )
        )


def insert_feedback(session: Session, *, run_id: UUID, thumb: str, reason: str | None) -> UUID:
    feedback_id = uuid4()
    session.add(
        FeedbackEventRecord(
            id=feedback_id,
            run_id=run_id,
            thumb=_sanitize_text(thumb) or "",
            reason=_sanitize_text(reason),
        )
    )
    return feedback_id


def get_admin_setting(session: Session, key: str) -> Any | None:
    rec = session.get(AdminSettingRecord, _sanitize_text(key) or "")
    if rec is None:
        return None
    return rec.value


def upsert_admin_setting(session: Session, key: str, value: Any) -> None:
    sanitized_key = _sanitize_text(key) or ""
    rec = session.get(AdminSettingRecord, sanitized_key)
    payload = _json_safe(value)
    if rec is None:
        session.add(AdminSettingRecord(key=sanitized_key, value=payload))
        return
    rec.value = payload
