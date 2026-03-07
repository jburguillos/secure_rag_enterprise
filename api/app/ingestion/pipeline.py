"""Ingestion orchestration pipeline."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client.models import PointStruct

from app.config import get_settings
from app.db.database import get_session
from app.db.repository import create_ingestion_run, finalize_ingestion_run, upsert_document
from app.ingestion.gdrive_connector import load_drive_documents, modified_time_from_document
from app.ingestion.local_connector import load_local_documents
from app.ingestion.multimodal import extract_pdf_page_images
from app.ingestion.parser import TextNode, chunk_documents
from app.models.schemas import IngestResponse
from app.retrieval.embeddings import EmbeddingService
from app.retrieval.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


class IngestionService:
    """Runs ingestion for Google Drive and local folder sources."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.qdrant = QdrantService()
        self.embeddings = EmbeddingService()

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_point_id(seed: str) -> str:
        return str(uuid5(NAMESPACE_URL, seed))

    def _text_nodes_to_points(self, nodes: list[TextNode]) -> tuple[list[PointStruct], int]:
        if not nodes:
            return [], 768
        texts = [node.text for node in nodes]
        vectors = self.embeddings.embed_batch(texts)
        vector_size = len(vectors[0]) if vectors else 768
        points: list[PointStruct] = []
        for node, vector in zip(nodes, vectors, strict=False):
            payload = dict(node.metadata)
            payload.setdefault("source", payload.get("dataset_source", "unknown"))
            payload.setdefault("page", None)
            payload.setdefault("modality", "text")
            points.append(
                PointStruct(
                    id=self._stable_point_id(node.node_id),
                    vector=vector,
                    payload=payload,
                )
            )
        return points, vector_size

    def _image_nodes_to_points(self, image_nodes: list[dict[str, Any]]) -> tuple[list[PointStruct], int]:
        if not image_nodes:
            return [], 768
        texts = [str(n.get("ocr_text") or n.get("name") or "") for n in image_nodes]
        vectors = self.embeddings.embed_batch(texts)
        vector_size = len(vectors[0]) if vectors else 768
        points: list[PointStruct] = []
        for node, vector in zip(image_nodes, vectors, strict=False):
            points.append(
                PointStruct(
                    id=self._stable_point_id(str(node["node_id"])),
                    vector=vector,
                    payload=node,
                )
            )
        return points, vector_size

    def _extract_local_pdf_image_nodes(self, docs: list) -> list[dict[str, Any]]:
        image_nodes: list[dict[str, Any]] = []
        output_root = self.settings.pdf_image_root
        for doc in docs:
            metadata = dict(doc.metadata or {})
            doc_id = str(metadata.get("doc_id") or doc.id_)
            source_path = metadata.get("source_path")
            if not source_path or not str(source_path).lower().endswith(".pdf"):
                continue
            extracted = extract_pdf_page_images(
                pdf_path=str(source_path),
                output_root=output_root,
                doc_id=doc_id,
                base_metadata=metadata,
                enable_ocr=self.settings.enable_ocr,
            )
            for node in extracted:
                image_nodes.append(node.metadata)
        return image_nodes

    def _persist_documents(self, docs: list, source: str) -> None:
        with get_session() as session:
            for doc in docs:
                md = dict(doc.metadata or {})
                doc_id = str(md.get("doc_id") or doc.id_)
                if not doc_id:
                    continue
                content = doc.text or doc.get_content()
                upsert_document(
                    session,
                    doc_id=doc_id,
                    source=source,
                    title=md.get("title") or md.get("name"),
                    mime_type=md.get("mimeType"),
                    modified_time=modified_time_from_document(doc) if source == "google_drive" else datetime.now(timezone.utc),
                    content_hash=self._hash_text(content),
                    permissions_summary={
                        "allowed_emails": md.get("allowed_emails", []),
                        "allowed_domains": md.get("allowed_domains", []),
                        "allowed_users": md.get("allowed_users", []),
                        "allowed_groups": md.get("allowed_groups", []),
                        "is_public": md.get("is_public", False),
                    },
                    metadata=md,
                )

    def ingest_gdrive(self, *, folder_id: str, auth_mode: str, dry_run: bool, dataset_source: str) -> IngestResponse:
        with get_session() as session:
            run_id = create_ingestion_run(
                session,
                source="google_drive",
                dataset_source=dataset_source,
                metadata={"folder_id": folder_id, "auth_mode": auth_mode},
            )

        docs, skipped = load_drive_documents(folder_id=folder_id, auth_mode=auth_mode)
        errors: list[str] = []
        added = 0
        updated = 0
        deleted = 0

        if not dry_run:
            text_nodes = chunk_documents(docs)
            points, vector_size = self._text_nodes_to_points(text_nodes)

            doc_ids = {str((doc.metadata or {}).get("doc_id") or doc.id_) for doc in docs}
            for doc_id in doc_ids:
                if doc_id:
                    self.qdrant.delete_document_nodes(self.settings.qdrant_text_collection, doc_id)

            self.qdrant.upsert_nodes(self.settings.qdrant_text_collection, points, vector_size)
            self._persist_documents(docs, source="google_drive")

        added = len(docs)

        with get_session() as session:
            finalize_ingestion_run(
                session,
                run_id=run_id,
                added=added,
                updated=updated,
                deleted=deleted,
                skipped=len(skipped),
                errors=errors,
            )

        return IngestResponse(
            ingestion_run_id=run_id,
            added=added,
            updated=updated,
            deleted=deleted,
            skipped=len(skipped),
            errors=[str(s) for s in skipped] + errors,
        )

    def ingest_local(self, *, path: str, acl_sidecar_path: str, dry_run: bool, dataset_source: str) -> IngestResponse:
        with get_session() as session:
            run_id = create_ingestion_run(
                session,
                source="local_folder",
                dataset_source=dataset_source,
                metadata={"path": path, "acl_sidecar_path": acl_sidecar_path},
            )

        docs, skipped = load_local_documents(path=path, acl_sidecar_path=acl_sidecar_path)
        errors: list[str] = []

        if not dry_run:
            text_nodes = chunk_documents(docs)
            points, vector_size = self._text_nodes_to_points(text_nodes)
            doc_ids = {str((doc.metadata or {}).get("doc_id") or doc.id_) for doc in docs}
            for doc_id in doc_ids:
                if doc_id:
                    self.qdrant.delete_document_nodes(self.settings.qdrant_text_collection, doc_id)

            self.qdrant.upsert_nodes(self.settings.qdrant_text_collection, points, vector_size)

            image_nodes = self._extract_local_pdf_image_nodes(docs)
            if image_nodes:
                image_points, image_vector_size = self._image_nodes_to_points(image_nodes)
                self.qdrant.upsert_nodes(self.settings.qdrant_image_collection, image_points, image_vector_size)

            self._persist_documents(docs, source="local_folder")

        with get_session() as session:
            finalize_ingestion_run(
                session,
                run_id=run_id,
                added=len(docs),
                updated=0,
                deleted=0,
                skipped=len(skipped),
                errors=errors,
            )

        return IngestResponse(
            ingestion_run_id=run_id,
            added=len(docs),
            updated=0,
            deleted=0,
            skipped=len(skipped),
            errors=[str(s) for s in skipped] + errors,
        )
