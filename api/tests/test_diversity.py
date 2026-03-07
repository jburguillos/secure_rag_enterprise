from __future__ import annotations

from app.retrieval.diversity import diversify_by_doc
from app.retrieval.qdrant_service import RetrievedNode


def _node(doc_id: str, idx: int) -> RetrievedNode:
    return RetrievedNode(
        node_id=f"{doc_id}::{idx}",
        score=1.0,
        text=f"text-{doc_id}-{idx}",
        payload={"doc_id": doc_id, "node_id": f"{doc_id}::{idx}"},
    )


def test_diversify_by_doc_round_robin() -> None:
    nodes = [_node("docA", 0), _node("docA", 1), _node("docB", 0), _node("docC", 0)]
    out = diversify_by_doc(nodes, per_doc_max=1, final_k=3)

    assert [n.payload["doc_id"] for n in out] == ["docA", "docB", "docC"]


def test_diversify_by_doc_respects_per_doc_cap() -> None:
    nodes = [_node("docA", 0), _node("docA", 1), _node("docA", 2), _node("docB", 0), _node("docB", 1)]
    out = diversify_by_doc(nodes, per_doc_max=2, final_k=5)

    docs = [n.payload["doc_id"] for n in out]
    assert docs.count("docA") == 2
    assert docs.count("docB") == 2
