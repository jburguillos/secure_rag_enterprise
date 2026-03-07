"""Local reranker for second-stage candidate refinement."""

from __future__ import annotations

import math
import re

from app.retrieval.embeddings import EmbeddingService
from app.retrieval.qdrant_service import RetrievedNode

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in TOKEN_RE.finditer(text)}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    if size == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(size))
    na = math.sqrt(sum(a[i] * a[i] for i in range(size)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(size)))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _lexical_overlap(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    cand = _tokenize(text)
    if not cand:
        return 0.0
    return len(query_tokens & cand) / float(len(query_tokens))


def _normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


class LocalReranker:
    """Embedding-assisted local reranker."""

    def __init__(self) -> None:
        self.embeddings = EmbeddingService()

    def rerank(self, *, query: str, candidates: list[RetrievedNode], top_k: int) -> list[RetrievedNode]:
        if not candidates:
            return []
        if top_k <= 0:
            return []

        query_vec = self.embeddings.embed_text(query)
        candidate_texts = [node.text or "" for node in candidates]
        candidate_vecs = self.embeddings.embed_batch(candidate_texts)
        query_tokens = _tokenize(query)

        prior_scores = _normalize_scores([float(node.score) for node in candidates])

        scored: list[tuple[RetrievedNode, float]] = []
        for node, vec, prior in zip(candidates, candidate_vecs, prior_scores, strict=False):
            semantic = _cosine(query_vec, vec)
            lexical = _lexical_overlap(query_tokens, node.text or "")
            final = 0.60 * semantic + 0.25 * lexical + 0.15 * prior
            node.score = final
            scored.append((node, final))

        ranked = [node for node, _ in sorted(scored, key=lambda item: item[1], reverse=True)]
        return ranked[:top_k]
