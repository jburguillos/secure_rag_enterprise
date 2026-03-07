from __future__ import annotations

from app.generation.guardrails import enforce_citation_requirement, should_refuse_for_insufficient_evidence


def test_refuse_if_no_evidence() -> None:
    assert should_refuse_for_insufficient_evidence(0)


def test_allow_when_evidence_exists() -> None:
    assert not should_refuse_for_insufficient_evidence(2)


def test_citation_requirement_enforced() -> None:
    ok, reason = enforce_citation_requirement(0)
    assert not ok
    assert reason == "citation_requirement_not_met"


def test_citation_requirement_passes() -> None:
    ok, reason = enforce_citation_requirement(2)
    assert ok
    assert reason is None
