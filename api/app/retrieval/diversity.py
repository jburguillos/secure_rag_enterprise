"""Evidence diversification helpers."""

from __future__ import annotations

from collections import defaultdict, deque

from app.retrieval.qdrant_service import RetrievedNode


def _doc_id(node: RetrievedNode) -> str:
    payload = node.payload or {}
    return str(payload.get("doc_id") or payload.get("file_id") or "unknown_doc")


def diversify_by_doc(nodes: list[RetrievedNode], *, per_doc_max: int, final_k: int) -> list[RetrievedNode]:
    """Round-robin select nodes while limiting max chunks per document."""

    if not nodes or per_doc_max <= 0 or final_k <= 0:
        return []

    grouped: dict[str, deque[RetrievedNode]] = defaultdict(deque)
    order: list[str] = []
    for node in nodes:
        did = _doc_id(node)
        if did not in grouped:
            order.append(did)
        grouped[did].append(node)

    counts: dict[str, int] = defaultdict(int)
    out: list[RetrievedNode] = []

    while len(out) < final_k:
        progressed = False
        for did in order:
            if len(out) >= final_k:
                break
            if counts[did] >= per_doc_max:
                continue
            if not grouped[did]:
                continue
            out.append(grouped[did].popleft())
            counts[did] += 1
            progressed = True
        if not progressed:
            break

    return out
