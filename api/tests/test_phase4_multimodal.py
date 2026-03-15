from __future__ import annotations

import pytest

from app.config import get_settings
from app.generation.service import generate_grounded_answer
from app.generation.vlm_router import VLMRouter
from app.models.schemas import Citation
from app.retrieval.hybrid import rrf_fuse
from app.retrieval.qdrant_service import RetrievedNode


def _node(node_id: str) -> RetrievedNode:
    return RetrievedNode(node_id=node_id, score=0.0, text=f"text-{node_id}", payload={"node_id": node_id, "modality": "text"})


def test_rrf_fuse_prioritizes_consensus_documents() -> None:
    ranking_a = [_node("a"), _node("b"), _node("c")]
    ranking_b = [_node("b"), _node("d"), _node("e")]

    fused = rrf_fuse([ranking_a, ranking_b], k=60)

    assert fused[0].node_id == "b"
    assert fused[0].score > fused[1].score


@pytest.mark.asyncio
async def test_vlm_router_noop_when_disabled_or_without_images() -> None:
    disabled = VLMRouter(enabled=False)
    result = await disabled.maybe_route(prompt="q", image_paths=["/tmp/a.png"])
    assert result.used_vlm is False
    assert result.answer == ""

    enabled = VLMRouter(enabled=True)
    result_no_images = await enabled.maybe_route(prompt="q", image_paths=[])
    assert result_no_images.used_vlm is False
    assert result_no_images.answer == ""


@pytest.mark.asyncio
async def test_generate_grounded_prefers_vlm_route_when_enabled(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "vlm_router", "mock", raising=False)
    monkeypatch.setattr(settings, "vlm_router_max_images", 3, raising=False)

    calls = {"vlm": 0, "ollama": 0}

    async def _fake_vlm(self, prompt: str, image_paths: list[str]):  # type: ignore[no-untyped-def]
        calls["vlm"] += 1
        assert image_paths == ["/tmp/page_1.png"]
        return type("VLMResultLike", (), {"used_vlm": True, "answer": "The table shows a 10M commitment [1]."})()

    async def _fake_generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0):  # type: ignore[no-untyped-def]
        calls["ollama"] += 1
        return "Fallback answer [1]"

    monkeypatch.setattr("app.generation.service.VLMRouter.maybe_route", _fake_vlm)
    monkeypatch.setattr("app.generation.service.OllamaClient.generate", _fake_generate)

    evidence = [
        RetrievedNode(
            node_id="img-1",
            score=0.9,
            text="Commitment table snapshot",
            payload={
                "modality": "image",
                "image_path": "/tmp/page_1.png",
                "doc_id": "commitments-xlsx",
                "name": "2025_LP_Commitment_Register.xlsx",
            },
        )
    ]
    citations = [
        Citation(
            doc_id="commitments-xlsx",
            doc_name="2025_LP_Commitment_Register.xlsx",
            node_id="img-1",
            modality="image",
        )
    ]

    result = await generate_grounded_answer(
        query="What is the total commitment?",
        mode="qa",
        evidence=evidence,
        citations=citations,
        include_images=True,
    )

    assert result.refusal_reason is None
    assert result.used_citation_indices == [1]
    assert "10M commitment" in result.answer
    assert calls["vlm"] == 1
    assert calls["ollama"] == 0
