from __future__ import annotations

from app.retrieval.qdrant_service import RetrievedNode
from app.retrieval.reranker import LocalReranker


def test_local_reranker_prefers_semantic_and_lexical_match(monkeypatch) -> None:
    def fake_embed_text(self, text: str) -> list[float]:
        t = text.lower()
        if "venture" in t or "capital" in t:
            return [1.0, 0.0]
        return [0.0, 1.0]

    def fake_embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [fake_embed_text(self, text) for text in texts]

    monkeypatch.setattr("app.retrieval.reranker.EmbeddingService.embed_text", fake_embed_text)
    monkeypatch.setattr("app.retrieval.reranker.EmbeddingService.embed_batch", fake_embed_batch)

    candidates = [
        RetrievedNode(
            node_id="n-good",
            score=0.2,
            text="Venture capital firms invest in startups and seed rounds.",
            payload={"doc_id": "doc1"},
        ),
        RetrievedNode(
            node_id="n-bad",
            score=0.9,
            text="Holiday schedule and cafeteria menu.",
            payload={"doc_id": "doc2"},
        ),
    ]

    reranker = LocalReranker()
    ranked = reranker.rerank(query="venture capital in Spain", candidates=candidates, top_k=2)

    assert ranked[0].node_id == "n-good"
