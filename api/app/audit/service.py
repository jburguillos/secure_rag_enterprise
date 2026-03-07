"""Audit logging services."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings, get_yaml_config
from app.db import repository


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pseudonymize_user(user_id: str | None, email: str | None) -> str | None:
    candidate = (user_id or email or "").strip().lower()
    if not candidate:
        return None
    return sha256_text(candidate)


def persist_policy_decision(
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
    repository.insert_policy_decision(
        session,
        decision_id=decision_id,
        run_id=run_id,
        user_id_hash=user_id_hash,
        user_groups=user_groups,
        policy_input=policy_input,
        policy_result=policy_result,
        policy_version=policy_version,
    )


def persist_query_audit(
    session: Session,
    *,
    run_id: UUID,
    query: str,
    mode: str,
    response_status: str,
    refusal_reason: str | None,
    user_id: str | None,
    email: str | None,
    groups: list[str],
    evidence_rows: list[dict[str, Any]],
    citation_rows: list[dict[str, Any]],
    policy_decision_id: UUID | None,
    model_id: str,
    model_version: str,
) -> None:
    settings = get_settings()
    yaml_cfg = get_yaml_config()
    user_hash = pseudonymize_user(user_id, email)
    query_hash = sha256_text(query)
    raw_query = query if settings.audit_raw_query else None

    repository.insert_query_run(
        session,
        run_id=run_id,
        user_id_hash=user_hash,
        user_groups=groups,
        query_hash=query_hash,
        raw_query=raw_query,
        mode=mode,
        response_status=response_status,
        refusal_reason=refusal_reason,
        model_id=model_id,
        model_version=model_version,
        config_version=str(yaml_cfg.get("app", {}).get("config_version", "unknown")),
        policy_decision_id=policy_decision_id,
    )
    session.flush()

    repository.insert_evidence_rows(session, run_id=run_id, evidence_rows=evidence_rows)
    repository.insert_citation_rows(session, run_id=run_id, citation_rows=citation_rows)
