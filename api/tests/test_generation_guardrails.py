from __future__ import annotations

import pytest

from app.generation.service import generate_grounded_answer
from app.models.schemas import Citation
from app.retrieval.qdrant_service import RetrievedNode


@pytest.mark.asyncio
async def test_generate_refuses_without_citations() -> None:
    evidence = [RetrievedNode(node_id="n1", score=0.9, text="Some factual text", payload={"modality": "text"})]
    result = await generate_grounded_answer(
        query="What is X?",
        mode="qa",
        evidence=evidence,
        citations=[],
        include_images=False,
    )
    assert result.refusal_reason == "citation_requirement_not_met"


@pytest.mark.asyncio
async def test_generate_refuses_when_answer_has_no_citation_markers(monkeypatch) -> None:
    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        return "Venture capital is financing for startups."

    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [RetrievedNode(node_id="n1", score=0.9, text="VC finances startups.", payload={"modality": "text"})]
    citations = [Citation(doc_id="d1", node_id="n1", modality="text")]

    result = await generate_grounded_answer(
        query="What is venture capital?",
        mode="qa",
        evidence=evidence,
        citations=citations,
        include_images=False,
    )

    assert result.refusal_reason == "citation_requirement_not_met"


@pytest.mark.asyncio
async def test_generate_visual_fallback_without_text() -> None:
    evidence = [RetrievedNode(node_id="i1", score=0.9, text="", payload={"modality": "image"})]
    result = await generate_grounded_answer(
        query="Describe the chart",
        mode="qa",
        evidence=evidence,
        citations=[Citation(doc_id="d1", node_id="i1", modality="image")],
        include_images=True,
    )
    assert result.refusal_reason == "visual_evidence_without_text"


@pytest.mark.asyncio
async def test_generate_returns_explicit_message_when_llm_unavailable(monkeypatch) -> None:
    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        raise RuntimeError("ollama unavailable")

    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [RetrievedNode(node_id="n1", score=0.9, text="VC finances startups.", payload={"modality": "text"})]
    citations = [Citation(doc_id="d1", node_id="n1", modality="text")]

    result = await generate_grounded_answer(
        query="What is venture capital?",
        mode="qa",
        evidence=evidence,
        citations=citations,
        include_images=False,
    )

    assert result.refusal_reason == "llm_unavailable"
    assert "local chat model is unavailable" in result.answer.lower()


@pytest.mark.asyncio
async def test_generate_attaches_missing_citation_when_sentence_matches_evidence(monkeypatch) -> None:
    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        return "Plus Partners is a venture capital firm in Spain that invests in startups."

    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [
        RetrievedNode(
            node_id="n1",
            score=0.9,
            text="Plus Partners is a venture capital firm in Spain that invests in startups, specifically preseed and seed companies.",
            payload={"modality": "text", "doc_id": "plus-partners", "name": "Plus Partners.txt"},
        )
    ]
    citations = [Citation(doc_id="plus-partners", doc_name="Plus Partners.txt", node_id="n1", modality="text")]

    result = await generate_grounded_answer(
        query="What does Plus Partners do?",
        mode="qa",
        evidence=evidence,
        citations=citations,
        include_images=False,
    )

    assert result.refusal_reason is None
    assert result.used_citation_indices == [1]
    assert "[1]" in result.answer


@pytest.mark.asyncio
async def test_generate_multidoc_summary_defaults_to_integrated_synthesis(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        captured["user_prompt"] = user_prompt
        return "Integrated summary [1] [2]"

    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [
        RetrievedNode(
            node_id="n1",
            score=0.9,
            text="Early venture capital studies relied on IPO prospectuses.",
            payload={"modality": "text", "doc_id": "vc-paper", "name": "VCPaper.pdf"},
        ),
        RetrievedNode(
            node_id="n2",
            score=0.8,
            text="Performance data can be verified by GPs via public filings.",
            payload={"modality": "text", "doc_id": "plus-paper", "name": "Plus Partners.txt"},
        ),
    ]
    citations = [
        Citation(doc_id="vc-paper", doc_name="VCPaper.pdf", node_id="n1", modality="text"),
        Citation(doc_id="plus-paper", doc_name="Plus Partners.txt", node_id="n2", modality="text"),
    ]

    result = await generate_grounded_answer(
        query="Summarize the most relevant venture capital findings with citations.",
        mode="summarize",
        evidence=evidence,
        citations=citations,
        include_images=False,
    )

    assert result.refusal_reason is None
    assert "Do not force one bullet per document." in captured["user_prompt"]
    assert "one bullet per document." not in captured["user_prompt"].split("Do not force one bullet per document.")[0]


@pytest.mark.asyncio
async def test_generate_multidoc_summary_uses_per_document_format_when_requested(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        captured["user_prompt"] = user_prompt
        return "- VCPaper [1]\n- Plus Partners [2]"

    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [
        RetrievedNode(
            node_id="n1",
            score=0.9,
            text="Early venture capital studies relied on IPO prospectuses.",
            payload={"modality": "text", "doc_id": "vc-paper", "name": "VCPaper.pdf"},
        ),
        RetrievedNode(
            node_id="n2",
            score=0.8,
            text="Performance data can be verified by GPs via public filings.",
            payload={"modality": "text", "doc_id": "plus-paper", "name": "Plus Partners.txt"},
        ),
    ]
    citations = [
        Citation(doc_id="vc-paper", doc_name="VCPaper.pdf", node_id="n1", modality="text"),
        Citation(doc_id="plus-paper", doc_name="Plus Partners.txt", node_id="n2", modality="text"),
    ]

    result = await generate_grounded_answer(
        query="Summarize each document in one bullet with citations.",
        mode="summarize",
        evidence=evidence,
        citations=citations,
        include_images=False,
    )

    assert result.refusal_reason is None
    assert "Produce a concise final summary with one bullet per document." in captured["user_prompt"]


@pytest.mark.asyncio
async def test_grounded_system_prompt_includes_domain_orientation(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        captured["system_prompt"] = system_prompt
        return "Grounded answer [1]"

    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [RetrievedNode(node_id="n1", score=0.9, text="VC finances startups.", payload={"modality": "text"})]
    citations = [Citation(doc_id="d1", node_id="n1", modality="text")]

    result = await generate_grounded_answer(
        query="What is venture capital?",
        mode="qa",
        evidence=evidence,
        citations=citations,
        include_images=False,
    )

    assert result.refusal_reason is None
    assert "venture capital fund operating model" in captured["system_prompt"].lower()
