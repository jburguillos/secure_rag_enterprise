"""Run inspection routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.db.database import get_session
from app.db.models import QueryCitationRecord, QueryEvidenceRecord, QueryRunRecord

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("/{run_id}")
def get_run(run_id: str):
    try:
        run_uuid = UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid run_id format") from exc

    with get_session() as session:
        run = session.get(QueryRunRecord, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail="run_id not found")

        evidence = session.execute(select(QueryEvidenceRecord).where(QueryEvidenceRecord.run_id == run_uuid)).scalars().all()
        citations = session.execute(select(QueryCitationRecord).where(QueryCitationRecord.run_id == run_uuid)).scalars().all()

        run_payload = {
            "run_id": str(run.run_id),
            "timestamp": run.timestamp,
            "response_status": run.response_status,
            "refusal_reason": run.refusal_reason,
            "policy_decision_id": str(run.policy_decision_id) if run.policy_decision_id else None,
        }

        evidence_payload = [
            {
                "node_id": row.node_id,
                "doc_id": row.doc_id,
                "page": row.page,
                "chunk_id": row.chunk_id,
                "modality": row.modality,
                "score": row.score,
                "payload": row.payload,
            }
            for row in evidence
        ]
        evidence_by_node_id = {
            row["node_id"]: row.get("payload") or {}
            for row in evidence_payload
        }

        citation_payload = [
            {
                "node_id": row.node_id,
                "doc_id": row.doc_id,
                "page": row.page,
                "chunk_id": row.chunk_id,
                "modality": row.modality,
                "sheet_name": evidence_by_node_id.get(row.node_id, {}).get("sheet_name"),
                "cell_range": evidence_by_node_id.get(row.node_id, {}).get("cell_range"),
                "row_start": evidence_by_node_id.get(row.node_id, {}).get("row_start"),
                "row_end": evidence_by_node_id.get(row.node_id, {}).get("row_end"),
                "tabular_node_type": evidence_by_node_id.get(row.node_id, {}).get("tabular_node_type"),
            }
            for row in citations
        ]

    return {
        **run_payload,
        "retrieved_evidence": evidence_payload,
        "citations": citation_payload,
    }
