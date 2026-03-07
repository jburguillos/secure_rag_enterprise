from __future__ import annotations

import pytest

from app.generation.service import generate_grounded_answer
from app.models.schemas import Citation
from app.retrieval.qdrant_service import RetrievedNode


@pytest.mark.asyncio
async def test_summarize_mode_uses_map_reduce_prompt(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        captured["system"] = system_prompt
        captured["prompt"] = user_prompt
        return "Summary [1] [2]"

    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [
        RetrievedNode(
            node_id="docA::n0",
            score=0.9,
            text="Doc A discusses private RAG architecture and controls.",
            payload={"doc_id": "docA", "name": "Doc A"},
        ),
        RetrievedNode(
            node_id="docB::n0",
            score=0.8,
            text="Doc B explains venture capital datasets and limitations.",
            payload={"doc_id": "docB", "name": "Doc B"},
        ),
    ]
    citations = [
        Citation(doc_id="docA", node_id="docA::n0", doc_name="Doc A"),
        Citation(doc_id="docB", node_id="docB::n0", doc_name="Doc B"),
    ]

    result = await generate_grounded_answer(
        query="Summarize docs",
        mode="summarize",
        evidence=evidence,
        citations=citations,
        include_images=False,
    )

    assert result.refusal_reason is None
    assert "Per-document map summaries" in captured["prompt"]
    assert "Map blocks:" in captured["prompt"]
