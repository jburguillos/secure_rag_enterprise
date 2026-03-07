"""Qdrant persistence and retrieval helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.config import get_settings


@dataclass
class RetrievedNode:
    node_id: str
    score: float
    text: str
    payload: dict[str, Any]


class QdrantService:
    """Wrapper around Qdrant client."""

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = QdrantClient(url=settings.qdrant_url, check_compatibility=False)

    def ensure_collection(self, collection_name: str, vector_size: int = 768) -> None:
        if self.client.collection_exists(collection_name):
            return
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

    def upsert_nodes(self, collection_name: str, points: list[PointStruct], vector_size: int) -> None:
        self.ensure_collection(collection_name, vector_size=vector_size)
        if points:
            self.client.upsert(collection_name=collection_name, points=points)

    def delete_document_nodes(self, collection_name: str, doc_id: str) -> None:
        if not self.client.collection_exists(collection_name):
            return
        self.client.delete(
            collection_name=collection_name,
            points_selector=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
        )

    @staticmethod
    def _to_retrieved_node(point_id: Any, score: float, payload: dict[str, Any] | None) -> RetrievedNode:
        payload_dict = dict(payload or {})
        return RetrievedNode(
            node_id=str(payload_dict.get("node_id") or point_id),
            score=float(score),
            text=str(payload_dict.get("text", "")),
            payload=payload_dict,
        )

    def dense_search(self, collection_name: str, query_vector: list[float], acl_filter: Filter, top_k: int) -> list[RetrievedNode]:
        self.ensure_collection(collection_name, vector_size=len(query_vector))

        if hasattr(self.client, "search"):
            matches = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                query_filter=acl_filter,
                with_payload=True,
                limit=top_k,
            )
            return [
                self._to_retrieved_node(
                    getattr(m, "id", None),
                    float(getattr(m, "score", 0.0)),
                    getattr(m, "payload", None),
                )
                for m in matches
            ]

        response = self.client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=acl_filter,
            with_payload=True,
            limit=top_k,
        )
        points = list(getattr(response, "points", []) or [])
        return [
            self._to_retrieved_node(
                getattr(p, "id", None),
                float(getattr(p, "score", 0.0)),
                getattr(p, "payload", None),
            )
            for p in points
        ]

    def filtered_scroll(self, collection_name: str, acl_filter: Filter, limit: int = 200) -> list[RetrievedNode]:
        if not self.client.collection_exists(collection_name):
            return []
        points, _ = self.client.scroll(
            collection_name=collection_name,
            scroll_filter=acl_filter,
            with_payload=True,
            with_vectors=False,
            limit=limit,
        )
        return [
            self._to_retrieved_node(
                getattr(p, "id", None),
                0.0,
                getattr(p, "payload", None),
            )
            for p in points
        ]
