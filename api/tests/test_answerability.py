from __future__ import annotations

import pytest

from app.models.schemas import Citation
from app.retrieval.answerability import AnswerabilityDecision, judge_answerability
from app.retrieval.qdrant_service import RetrievedNode


class _JudgeSettings:
    def __init__(self, *, enabled: bool = True, use_llm: bool = False) -> None:
        self.enable_answerability_judge = enabled
        self.answerability_use_llm = use_llm
        self.answerability_max_evidence_nodes = 6
        self.answerability_max_chars_per_node = 900


@pytest.mark.asyncio
async def test_answerability_heuristic_allows_topic_supported_query(monkeypatch) -> None:
    monkeypatch.setattr("app.retrieval.answerability.get_settings", lambda: _JudgeSettings(enabled=True, use_llm=False))

    evidence = [
        RetrievedNode(
            node_id="node-1",
            score=0.9,
            text="Venture capital funds invest in startups and scale-ups.",
            payload={"doc_id": "doc-1"},
        )
    ]
    citations = [Citation(doc_id="doc-1", node_id="node-1")]

    result = await judge_answerability(
        query="Help me research venture capital trends",
        mode="qa",
        evidence=evidence,
        citations=citations,
    )

    assert result.answerable is True
    assert result.support_indices == [1]
    assert result.source == "heuristic"


@pytest.mark.asyncio
async def test_answerability_heuristic_allows_document_discovery_from_doc_title(monkeypatch) -> None:
    monkeypatch.setattr("app.retrieval.answerability.get_settings", lambda: _JudgeSettings(enabled=True, use_llm=False))

    evidence = [
        RetrievedNode(
            node_id="node-1",
            score=0.9,
            text="This document discusses private equity datasets and fund performance.",
            payload={"doc_id": "doc-1", "name": "VCPaper.pdf"},
        )
    ]
    citations = [Citation(doc_id="doc-1", doc_name="VCPaper.pdf", node_id="node-1")]

    result = await judge_answerability(
        query="Tienes algo de research o papers de VC?",
        mode="qa",
        evidence=evidence,
        citations=citations,
    )

    assert result.answerable is True
    assert result.reason == "document_discovery_supported"
    assert result.support_indices == [1]
    assert result.source == "heuristic"


@pytest.mark.asyncio
async def test_answerability_llm_result_is_used(monkeypatch) -> None:
    monkeypatch.setattr("app.retrieval.answerability.get_settings", lambda: _JudgeSettings(enabled=True, use_llm=True))

    async def _fake_generate_from_messages(self, **kwargs) -> str:
        return '{"answerable": true, "reason": "grounded_support", "support_indices": [2, 1]}'

    monkeypatch.setattr(
        "app.retrieval.answerability.OllamaClient.generate_from_messages",
        _fake_generate_from_messages,
    )

    evidence = [
        RetrievedNode(node_id="node-1", score=0.7, text="First text", payload={"doc_id": "doc-1"}),
        RetrievedNode(node_id="node-2", score=0.8, text="Second text", payload={"doc_id": "doc-2"}),
    ]
    citations = [
        Citation(doc_id="doc-1", node_id="node-1"),
        Citation(doc_id="doc-2", node_id="node-2"),
    ]

    result = await judge_answerability(
        query="What does the evidence say?",
        mode="qa",
        evidence=evidence,
        citations=citations,
    )

    assert result == AnswerabilityDecision(
        answerable=True,
        reason="grounded_support",
        support_indices=[2, 1],
        source="llm",
    )


@pytest.mark.asyncio
async def test_answerability_summary_prefers_heuristic_over_llm_denial(monkeypatch) -> None:
    monkeypatch.setattr("app.retrieval.answerability.get_settings", lambda: _JudgeSettings(enabled=True, use_llm=True))

    async def _fake_generate_from_messages(self, **kwargs) -> str:
        return '{"answerable": false, "reason": "too_strict", "support_indices": []}'

    monkeypatch.setattr(
        "app.retrieval.answerability.OllamaClient.generate_from_messages",
        _fake_generate_from_messages,
    )

    evidence = [
        RetrievedNode(node_id="node-1", score=0.8, text="First summary block", payload={"doc_id": "doc-1"}),
        RetrievedNode(node_id="node-2", score=0.7, text="Second summary block", payload={"doc_id": "doc-2"}),
    ]
    citations = [
        Citation(doc_id="doc-1", node_id="node-1"),
        Citation(doc_id="doc-2", node_id="node-2"),
    ]

    result = await judge_answerability(
        query="Summarize the documents you have",
        mode="summarize",
        evidence=evidence,
        citations=citations,
    )

    assert result.answerable is True
    assert result.reason == "summary_supported"
    assert result.support_indices == [1, 2]
    assert result.source == "heuristic"


@pytest.mark.asyncio
async def test_answerability_mention_query_prefers_heuristic_over_llm_denial(monkeypatch) -> None:
    monkeypatch.setattr("app.retrieval.answerability.get_settings", lambda: _JudgeSettings(enabled=True, use_llm=True))

    async def _fake_generate_from_messages(self, **kwargs) -> str:
        return '{"answerable": false, "reason": "llm_miss", "support_indices": []}'

    monkeypatch.setattr(
        "app.retrieval.answerability.OllamaClient.generate_from_messages",
        _fake_generate_from_messages,
    )

    evidence = [
        RetrievedNode(
            node_id="node-1",
            score=0.9,
            text="Early venture capital studies relied on information available in IPO prospectuses.",
            payload={"doc_id": "doc-1", "name": "VCPaper.pdf"},
        )
    ]
    citations = [Citation(doc_id="doc-1", doc_name="VCPaper.pdf", node_id="node-1")]

    result = await judge_answerability(
        query="Do any indexed documents mention IPO prospectuses?",
        mode="qa",
        evidence=evidence,
        citations=citations,
    )

    assert result.answerable is True
    assert result.support_indices == [1]
    assert result.source == "heuristic"
