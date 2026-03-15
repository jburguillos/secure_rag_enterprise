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
from app.db.repository import create_ingestion_run, fail_ingestion_run, finalize_ingestion_run, upsert_document
from app.ingestion.gdrive_connector import (
    build_drive_service,
    download_drive_file_bytes,
    load_drive_documents,
    modified_time_from_document,
)
from app.ingestion.local_connector import load_local_documents
from app.ingestion.multimodal import extract_pdf_page_images, extract_pdf_page_images_from_bytes
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
    def start_ingestion_run(*, source: str, dataset_source: str, metadata: dict[str, Any] | None = None):
        with get_session() as session:
            return create_ingestion_run(
                session,
                source=source,
                dataset_source=dataset_source,
                metadata=metadata,
            )

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_point_id(seed: str) -> str:
        return str(uuid5(NAMESPACE_URL, seed))

    def _text_nodes_to_points(self, nodes: list[TextNode]) -> tuple[list[PointStruct], int]:
        if not nodes:
            return [], 768
        texts = [str(node.metadata.get("embedding_text") or node.text) for node in nodes]
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
        texts = [str(n.get("ocr_text") or n.get("text") or n.get("name") or "") for n in image_nodes]
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

    @staticmethod
    def _annotate_image_nodes_with_text_links(image_nodes: list[dict[str, Any]], text_nodes: list[TextNode]) -> list[dict[str, Any]]:
        page_index: dict[tuple[str, int], list[TextNode]] = {}
        for node in text_nodes:
            page = node.metadata.get("page")
            if page is None:
                continue
            key = (node.doc_id, int(page))
            page_index.setdefault(key, []).append(node)

        enriched: list[dict[str, Any]] = []
        for image_node in image_nodes:
            payload = dict(image_node)
            doc_id = str(payload.get("doc_id") or "")
            page = payload.get("page")
            linked_nodes: list[TextNode] = []
            if doc_id and page is not None:
                linked_nodes = page_index.get((doc_id, int(page)), [])

            payload["linked_text_node_ids"] = [node.node_id for node in linked_nodes]
            payload["linked_chunk_ids"] = [node.chunk_id for node in linked_nodes]
            payload["linked_text_count"] = len(linked_nodes)
            if linked_nodes:
                payload["linked_text_preview"] = " ".join(
                    str(node.text or "").strip() for node in linked_nodes[:2] if str(node.text or "").strip()
                )[:700].strip()
            else:
                payload["linked_text_preview"] = ""
            enriched.append(payload)

        return enriched

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

    def _extract_drive_pdf_image_nodes(self, docs: list, drive_service) -> tuple[list[dict[str, Any]], list[str]]:
        image_nodes: list[dict[str, Any]] = []
        errors: list[str] = []
        output_root = self.settings.pdf_image_root

        for doc in docs:
            metadata = dict(doc.metadata or {})
            if metadata.get("mimeType") != "application/pdf":
                continue

            file_id = str(metadata.get("file_id") or metadata.get("doc_id") or doc.id_ or "")
            if not file_id:
                continue

            try:
                pdf_bytes = download_drive_file_bytes(
                    file_id=file_id,
                    mime_type="application/pdf",
                    service=drive_service,
                )
                extracted = extract_pdf_page_images_from_bytes(
                    pdf_bytes=pdf_bytes,
                    output_root=output_root,
                    doc_id=file_id,
                    base_metadata=metadata,
                    enable_ocr=self.settings.enable_ocr,
                )
                for node in extracted:
                    image_nodes.append(node.metadata)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Drive PDF image extraction failed for %s: %s", file_id, exc)
                errors.append(f"drive_pdf_image_extract_failed:{file_id}:{exc}")

        return image_nodes, errors

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
                    content_hash=str(md.get("hash") or self._hash_text(content)),
                    permissions_summary={
                        "allowed_emails": md.get("allowed_emails", []),
                        "allowed_domains": md.get("allowed_domains", []),
                        "allowed_users": md.get("allowed_users", []),
                        "allowed_groups": md.get("allowed_groups", []),
                        "is_public": md.get("is_public", False),
                    },
                    metadata=md,
                )

    def _delete_existing_doc_nodes(self, doc_ids: set[str]) -> None:
        for doc_id in doc_ids:
            if not doc_id:
                continue
            self.qdrant.delete_document_nodes(self.settings.qdrant_text_collection, doc_id)
            self.qdrant.delete_document_nodes(self.settings.qdrant_image_collection, doc_id)

    def ingest_gdrive(
        self,
        *,
        folder_id: str,
        auth_mode: str,
        dry_run: bool,
        dataset_source: str,
        run_id=None,
    ) -> IngestResponse:
        run_id = run_id or self.start_ingestion_run(
            source="google_drive",
            dataset_source=dataset_source,
            metadata={"folder_id": folder_id, "auth_mode": auth_mode},
        )

        try:
            errors: list[str] = []
            drive_service = build_drive_service(auth_mode)
            docs, skipped = load_drive_documents(folder_id=folder_id, auth_mode=auth_mode, service=drive_service)

            text_nodes_indexed = 0
            image_nodes_indexed = 0

            if not dry_run:
                text_nodes = chunk_documents(docs)
                text_nodes_indexed = len(text_nodes)
                points, vector_size = self._text_nodes_to_points(text_nodes)

                image_nodes, image_errors = self._extract_drive_pdf_image_nodes(docs, drive_service)
                image_nodes = self._annotate_image_nodes_with_text_links(image_nodes, text_nodes)
                image_nodes_indexed = len(image_nodes)
                errors.extend(image_errors)

                doc_ids = {str((doc.metadata or {}).get("doc_id") or doc.id_) for doc in docs}
                self._delete_existing_doc_nodes(doc_ids)

                self.qdrant.upsert_nodes(self.settings.qdrant_text_collection, points, vector_size)
                if image_nodes:
                    image_points, image_vector_size = self._image_nodes_to_points(image_nodes)
                    self.qdrant.upsert_nodes(self.settings.qdrant_image_collection, image_points, image_vector_size)

                self._persist_documents(docs, source="google_drive")

            added = len(docs)
            all_errors = [str(s) for s in skipped] + errors

            with get_session() as session:
                finalize_ingestion_run(
                    session,
                    run_id=run_id,
                    added=added,
                    updated=0,
                    deleted=0,
                    skipped=len(skipped),
                    errors=all_errors,
                    metadata_updates={
                        "text_nodes_indexed": text_nodes_indexed,
                        "image_nodes_indexed": image_nodes_indexed,
                    },
                )

            return IngestResponse(
                ingestion_run_id=run_id,
                added=added,
                updated=0,
                deleted=0,
                skipped=len(skipped),
                text_nodes_indexed=text_nodes_indexed,
                image_nodes_indexed=image_nodes_indexed,
                errors=all_errors,
            )
        except Exception as exc:  # noqa: BLE001
            with get_session() as session:
                fail_ingestion_run(session, run_id=run_id, errors=[str(exc)])
            raise

    def ingest_local(
        self,
        *,
        path: str,
        acl_sidecar_path: str,
        dry_run: bool,
        dataset_source: str,
        run_id=None,
    ) -> IngestResponse:
        run_id = run_id or self.start_ingestion_run(
            source="local_folder",
            dataset_source=dataset_source,
            metadata={"path": path, "acl_sidecar_path": acl_sidecar_path},
        )

        try:
            docs, skipped = load_local_documents(path=path, acl_sidecar_path=acl_sidecar_path)
            errors: list[str] = []
            text_nodes_indexed = 0
            image_nodes_indexed = 0

            if not dry_run:
                text_nodes = chunk_documents(docs)
                text_nodes_indexed = len(text_nodes)
                points, vector_size = self._text_nodes_to_points(text_nodes)
                doc_ids = {str((doc.metadata or {}).get("doc_id") or doc.id_) for doc in docs}
                self._delete_existing_doc_nodes(doc_ids)

                self.qdrant.upsert_nodes(self.settings.qdrant_text_collection, points, vector_size)

                image_nodes = self._extract_local_pdf_image_nodes(docs)
                image_nodes = self._annotate_image_nodes_with_text_links(image_nodes, text_nodes)
                image_nodes_indexed = len(image_nodes)
                if image_nodes:
                    image_points, image_vector_size = self._image_nodes_to_points(image_nodes)
                    self.qdrant.upsert_nodes(self.settings.qdrant_image_collection, image_points, image_vector_size)

                self._persist_documents(docs, source="local_folder")

            all_errors = [str(s) for s in skipped] + errors

            with get_session() as session:
                finalize_ingestion_run(
                    session,
                    run_id=run_id,
                    added=len(docs),
                    updated=0,
                    deleted=0,
                    skipped=len(skipped),
                    errors=all_errors,
                    metadata_updates={
                        "text_nodes_indexed": text_nodes_indexed,
                        "image_nodes_indexed": image_nodes_indexed,
                    },
                )

            return IngestResponse(
                ingestion_run_id=run_id,
                added=len(docs),
                updated=0,
                deleted=0,
                skipped=len(skipped),
                text_nodes_indexed=text_nodes_indexed,
                image_nodes_indexed=image_nodes_indexed,
                errors=all_errors,
            )
        except Exception as exc:  # noqa: BLE001
            with get_session() as session:
                fail_ingestion_run(session, run_id=run_id, errors=[str(exc)])
            raise
