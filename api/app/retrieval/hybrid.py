"""Hybrid and multimodal retrieval."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.auth.context import Entitlements
from app.config import get_settings
from app.models.schemas import QueryFilters
from app.retrieval.acl import build_acl_filter
from app.retrieval.diversity import diversify_by_doc
from app.retrieval.embeddings import EmbeddingService
from app.retrieval.filters import build_metadata_filter, combine_filters
from app.retrieval.qdrant_service import QdrantService, RetrievedNode
from app.retrieval.reranker import LocalReranker

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


@dataclass
class RetrievalBundle:
    evidence: list[RetrievedNode]
    text_evidence: list[RetrievedNode]
    image_evidence: list[RetrievedNode]


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(text)]


def rrf_fuse(rankings: list[list[RetrievedNode]], k: int = 60) -> list[RetrievedNode]:
    scores: dict[str, float] = defaultdict(float)
    best: dict[str, RetrievedNode] = {}
    for ranking in rankings:
        for idx, node in enumerate(ranking, start=1):
            scores[node.node_id] += 1.0 / (k + idx)
            if node.node_id not in best:
                best[node.node_id] = node

    fused = sorted(best.values(), key=lambda n: scores[n.node_id], reverse=True)
    for node in fused:
        node.score = scores[node.node_id]
    return fused


class RetrievalService:
    """Dense + lexical hybrid retrieval with ACL filters."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.qdrant = QdrantService()
        self.embeddings = EmbeddingService()
        self.reranker = LocalReranker() if self.settings.enable_rerank else None

    def _effective_top_k(self, top_k: int | None) -> int:
        requested = top_k or self.settings.top_k_fused
        return max(1, requested)

    def _candidate_top_k(self, final_top_k: int) -> int:
        multiplier = max(1, self.settings.retrieval_candidate_multiplier)
        broad = final_top_k * multiplier
        return max(final_top_k, min(broad, self.settings.retrieval_candidate_max))

    def _dense_text(self, query: str, entitlements: Entitlements, *, top_k: int, metadata_filter) -> list[RetrievedNode]:
        vector = self.embeddings.embed_text(query)
        acl = build_acl_filter(entitlements)
        query_filter = combine_filters(acl, metadata_filter)
        return self.qdrant.dense_search(
            collection_name=self.settings.qdrant_text_collection,
            query_vector=vector,
            acl_filter=query_filter,
            top_k=top_k,
        )

    def _bm25_text(self, query: str, entitlements: Entitlements, *, top_k: int, metadata_filter) -> list[RetrievedNode]:
        acl = build_acl_filter(entitlements)
        query_filter = combine_filters(acl, metadata_filter)
        candidate_limit = max(300, min(top_k * 10, 2000))
        candidates = self.qdrant.filtered_scroll(self.settings.qdrant_text_collection, acl_filter=query_filter, limit=candidate_limit)
        if not candidates:
            return []

        corpus = [_tokenize(node.text) for node in candidates]
        if not any(corpus):
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        bm25 = BM25Okapi(corpus)
        raw_scores = bm25.get_scores(query_tokens)
        scored = sorted(zip(candidates, raw_scores), key=lambda x: x[1], reverse=True)

        output: list[RetrievedNode] = []
        for node, score in scored[:top_k]:
            node.score = float(score)
            output.append(node)
        return output

    def retrieve_text(self, query: str, entitlements: Entitlements, *, top_k: int | None = None, query_filters: QueryFilters | None = None) -> list[RetrievedNode]:
        final_top_k = self._effective_top_k(top_k)
        candidate_top_k = self._candidate_top_k(final_top_k)
        metadata_filter = build_metadata_filter(query_filters)

        dense_nodes = self._dense_text(query, entitlements, top_k=candidate_top_k, metadata_filter=metadata_filter)
        bm25_nodes = self._bm25_text(query, entitlements, top_k=candidate_top_k, metadata_filter=metadata_filter)

        fused = rrf_fuse([dense_nodes, bm25_nodes], k=60)

        if self.reranker and fused:
            rerank_count = min(len(fused), max(final_top_k, self.settings.rerank_top_candidates))
            reranked_head = self.reranker.rerank(query=query, candidates=fused[:rerank_count], top_k=rerank_count)
            fused = reranked_head + fused[rerank_count:]

        diversified = diversify_by_doc(
            fused,
            per_doc_max=max(1, self.settings.retrieval_doc_diversity_max_chunks),
            final_k=final_top_k,
        )
        return diversified[:final_top_k]

    def retrieve_multimodal(
        self,
        query: str,
        entitlements: Entitlements,
        include_images: bool,
        *,
        top_k: int | None = None,
        query_filters: QueryFilters | None = None,
    ) -> RetrievalBundle:
        final_top_k = self._effective_top_k(top_k)
        metadata_filter = build_metadata_filter(query_filters)

        text_hits = self.retrieve_text(query, entitlements, top_k=final_top_k, query_filters=query_filters)
        image_hits: list[RetrievedNode] = []

        if include_images:
            vector = self.embeddings.embed_text(query)
            acl = build_acl_filter(entitlements)
            query_filter = combine_filters(acl, metadata_filter)
            try:
                image_hits = self.qdrant.dense_search(
                    collection_name=self.settings.qdrant_image_collection,
                    query_vector=vector,
                    acl_filter=query_filter,
                    top_k=final_top_k,
                )
            except Exception:
                image_hits = []

        fused = rrf_fuse([text_hits, image_hits], k=60)
        evidence = diversify_by_doc(
            fused,
            per_doc_max=max(1, self.settings.retrieval_doc_diversity_max_chunks),
            final_k=final_top_k,
        )

        return RetrievalBundle(
            evidence=evidence,
            text_evidence=text_hits,
            image_evidence=image_hits,
        )

    def retrieve_inventory(
        self,
        entitlements: Entitlements,
        *,
        query: str | None = None,
        query_filters: QueryFilters | None = None,
        limit: int = 5000,
    ) -> list[RetrievedNode]:
        """Return metadata-bearing text nodes for corpus inventory style queries.

        This bypasses semantic ranking and scrolls authorized nodes so the caller can
        answer from document metadata (titles, paths, mime types) rather than chunk text.
        The optional ``query`` parameter is accepted for compatibility with higher-level
        inventory and clarification flows that may pass it even though inventory retrieval
        itself does not use semantic ranking.
        """

        metadata_filter = build_metadata_filter(query_filters)
        acl = build_acl_filter(entitlements)
        query_filter = combine_filters(acl, metadata_filter)
        return self.qdrant.filtered_scroll(
            self.settings.qdrant_text_collection,
            acl_filter=query_filter,
            limit=max(1, limit),
        )
