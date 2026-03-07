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
