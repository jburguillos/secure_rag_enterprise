"""Query orchestration service."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.audit.service import persist_policy_decision, persist_query_audit
from app.auth.context import Entitlements
from app.config import get_settings
from app.db.database import get_session
from app.generation.service import generate_grounded_answer
from app.models.schemas import Citation, PolicyDecision, QueryRequest, QueryResponse
from app.policy.opa_client import PolicyClient, PolicyResult
from app.retrieval.acl import payload_access_allowed
from app.retrieval.diversity import diversify_by_doc
from app.retrieval.hybrid import RetrievalService


def _citation_from_payload(payload: dict[str, Any], node_id: str) -> Citation:
    return Citation(
        doc_id=str(payload.get("doc_id") or payload.get("file_id") or "unknown_doc"),
        doc_name=payload.get("name") or payload.get("title"),
        page=payload.get("page"),
        chunk_id=payload.get("chunk_id"),
        node_id=node_id,
        modality=str(payload.get("modality") or "text"),
        webViewLink=payload.get("webViewLink"),
    )


def _empty_resource_acl() -> dict[str, Any]:
    return {
        "allowed_users": [],
        "allowed_groups": [],
        "allowed_emails": [],
        "allowed_domains": [],
        "is_public": False,
    }


async def run_query_flow(request: QueryRequest, entitlements: Entitlements) -> QueryResponse:
    settings = get_settings()
    retrieval = RetrievalService()
    policy = PolicyClient()
    run_id = uuid4()

    requested_top_k = request.top_k if request.top_k and request.top_k > 0 else settings.top_k_fused
    effective_top_k = min(requested_top_k, settings.top_k_fused)

    bundle = retrieval.retrieve_multimodal(
        query=request.query,
        entitlements=entitlements,
        include_images=request.include_images,
        top_k=effective_top_k,
        query_filters=request.filters,
    )

    allowed_nodes = []
    effective_policy: PolicyResult | None = None

    if not bundle.evidence:
        effective_policy = await policy.evaluate(
            entitlements=entitlements,
            resource_acl=_empty_resource_acl(),
            transitional_drive_acl=True,
        )
    else:
        for node in bundle.evidence:
            payload = node.payload or {}
            if not payload_access_allowed(payload, entitlements):
                continue

            acl = {
                "allowed_users": payload.get("allowed_users") or [],
                "allowed_groups": payload.get("allowed_groups") or [],
                "allowed_emails": payload.get("allowed_emails") or [],
                "allowed_domains": payload.get("allowed_domains") or [],
                "is_public": bool(payload.get("is_public", False)),
            }
            decision = await policy.evaluate(entitlements=entitlements, resource_acl=acl, transitional_drive_acl=True)
            if decision.allow:
                allowed_nodes.append(node)
                effective_policy = decision

        if effective_policy is None:
            effective_policy = await policy.evaluate(
                entitlements=entitlements,
                resource_acl=_empty_resource_acl(),
                transitional_drive_acl=True,
            )

    generation_cap = min(effective_top_k, settings.generation_max_evidence_nodes)
    generation_nodes = diversify_by_doc(
        allowed_nodes,
        per_doc_max=max(1, settings.generation_doc_diversity_max_chunks),
        final_k=max(1, generation_cap),
    )
    citations = [_citation_from_payload(node.payload, node.node_id) for node in generation_nodes]

    generated = await generate_grounded_answer(
        query=request.query,
        mode=request.mode,
        evidence=generation_nodes,
        citations=citations,
        include_images=request.include_images,
    )

    response_status = "refused" if generated.refusal_reason else "answered"
    assert effective_policy is not None
    policy_model = PolicyDecision(
        decision_id=effective_policy.decision_id,
        allow=effective_policy.allow,
        reason=effective_policy.reason,
        policy_version=effective_policy.policy_version,
    )

    evidence_rows = [
        {
            "node_id": node.node_id,
            "doc_id": node.payload.get("doc_id") or node.payload.get("file_id"),
            "page": node.payload.get("page"),
            "chunk_id": node.payload.get("chunk_id"),
            "modality": node.payload.get("modality"),
            "score": node.score,
            "payload": node.payload,
        }
        for node in allowed_nodes
    ]
    citation_rows = [c.model_dump() for c in citations]

    with get_session() as session:
        persist_policy_decision(
            session,
            decision_id=policy_model.decision_id,
            run_id=run_id,
            user_id_hash=None,
            user_groups=entitlements.groups,
            policy_input={
                "query_hash_only": True,
                "filters": request.filters.model_dump() if request.filters else None,
                "effective_top_k": effective_top_k,
                "evidence_count": len(bundle.evidence),
                "allowed_count": len(allowed_nodes),
                "generation_count": len(generation_nodes),
            },
            policy_result=policy_model.model_dump(),
            policy_version=policy_model.policy_version,
        )
        persist_query_audit(
            session,
            run_id=run_id,
            query=request.query,
            mode=request.mode,
            response_status=response_status,
            refusal_reason=generated.refusal_reason,
            user_id=entitlements.user_id,
            email=entitlements.email,
            groups=entitlements.groups,
            evidence_rows=evidence_rows,
            citation_rows=citation_rows,
            policy_decision_id=policy_model.decision_id,
            model_id=settings.ollama_chat_model,
            model_version="local",
        )

    return QueryResponse(
        run_id=run_id,
        answer=generated.answer,
        refusal_reason=generated.refusal_reason,
        citations=citations,
        policy_decision=policy_model,
    )
