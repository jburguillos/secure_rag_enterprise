"""Generation guardrail checks."""

from __future__ import annotations

from app.config import get_settings


def should_refuse_for_insufficient_evidence(evidence_count: int) -> bool:
    return evidence_count <= 0


def enforce_citation_requirement(citation_count: int) -> tuple[bool, str | None]:
    settings = get_settings()
    if not settings.require_citations:
        return True, None
    if citation_count >= settings.min_citations:
        return True, None
    return False, "citation_requirement_not_met"
