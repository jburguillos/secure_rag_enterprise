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

    return {
        "run_id": str(run.run_id),
        "timestamp": run.timestamp,
        "response_status": run.response_status,
        "refusal_reason": run.refusal_reason,
        "policy_decision_id": str(run.policy_decision_id) if run.policy_decision_id else None,
        "retrieved_evidence": [
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
        ],
        "citations": [
            {
                "node_id": row.node_id,
                "doc_id": row.doc_id,
                "page": row.page,
                "chunk_id": row.chunk_id,
                "modality": row.modality,
            }
            for row in citations
        ],
    }
