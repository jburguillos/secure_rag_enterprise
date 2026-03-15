"""Document parsing and chunking utilities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
import re
from typing import Any

from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter

from app.config import get_yaml_config


@dataclass
class TextNode:
    node_id: str
    chunk_id: str
    doc_id: str
    text: str
    metadata: dict[str, Any]


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").strip())


def _merge_small_chunks(chunks: list[str], *, min_chars: int) -> list[str]:
    merged: list[str] = []
    for raw in chunks:
        chunk = _normalize_text(raw)
        if not chunk:
            continue
        if merged and len(chunk) < min_chars:
            merged[-1] = _normalize_text(f"{merged[-1]}\n\n{chunk}")
        else:
            merged.append(chunk)

    if len(merged) >= 2 and len(merged[-1]) < min_chars:
        merged[-2] = _normalize_text(f"{merged[-2]}\n\n{merged[-1]}")
        merged.pop()

    return merged


def _split_generic_text(text: str, splitter: SentenceSplitter, *, chunk_size: int, min_chars: int) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []

    blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
    if not blocks:
        blocks = [normalized]

    chunks: list[str] = []
    for block in blocks:
        if len(block) <= chunk_size:
            chunks.append(block)
            continue
        chunks.extend(splitter.split_text(block))
    return _merge_small_chunks(chunks, min_chars=min_chars)


def _build_embedding_text(chunk: str, metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    title = str(metadata.get("title") or metadata.get("name") or "").strip()
    if title:
        parts.append(f"title: {title}")

    page = metadata.get("page")
    if page is not None:
        parts.append(f"page: {page}")

    mime_type = str(metadata.get("mimeType") or "").strip()
    if mime_type:
        parts.append(f"type: {mime_type}")

    sheet_name = str(metadata.get("sheet_name") or "").strip()
    if sheet_name:
        parts.append(f"sheet: {sheet_name}")

    tabular_node_type = str(metadata.get("tabular_node_type") or "").strip()
    if tabular_node_type:
        parts.append(f"tabular_type: {tabular_node_type}")

    row_start = metadata.get("row_start")
    row_end = metadata.get("row_end")
    if row_start is not None and row_end is not None:
        parts.append(f"rows: {row_start}-{row_end}")

    cell_range = str(metadata.get("cell_range") or "").strip()
    if cell_range:
        parts.append(f"range: {cell_range}")

    headers = metadata.get("column_headers") or []
    if isinstance(headers, list) and headers:
        parts.append(f"headers: {', '.join(str(header) for header in headers[:12])}")

    parts.append(chunk)
    return "\n".join(parts)


def chunk_documents(documents: list[Document]) -> list[TextNode]:
    cfg = get_yaml_config()
    chunking_cfg = cfg.get("chunking", {})
    chunk_size = int(chunking_cfg.get("chunk_size", 450))
    chunk_overlap = int(chunking_cfg.get("chunk_overlap", 80))
    min_chunk_chars = int(chunking_cfg.get("min_chunk_chars", 180))
    splitter = SentenceSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    nodes: list[TextNode] = []
    for doc in documents:
        doc_id = str((doc.metadata or {}).get("doc_id") or doc.id_)
        base_metadata = dict(doc.metadata or {})
        page_map = list(base_metadata.pop("page_map", []) or [])
        base_metadata.pop("sheet_map", None)
        tabular_nodes = list(base_metadata.pop("tabular_nodes", []) or [])

        if tabular_nodes:
            for idx, raw_node in enumerate(tabular_nodes):
                chunk = _normalize_text(str(raw_node.get("text") or ""))
                if not chunk:
                    continue
                chunk_hash = _hash_text(chunk)
                node_type = str(raw_node.get("tabular_node_type") or "tabular")
                sheet_name = str(raw_node.get("sheet_name") or "")
                row_start = raw_node.get("row_start")
                row_end = raw_node.get("row_end")
                node_id = (
                    f"{doc_id}::{node_type}::{sheet_name or 'workbook'}::{row_start or 'na'}-{row_end or 'na'}::{chunk_hash[:8]}"
                )
                chunk_id = (
                    f"{doc_id}::{node_type}::{sheet_name or 'workbook'}::{row_start or 'na'}-{row_end or 'na'}"
                )
                metadata = {
                    **base_metadata,
                    **{k: v for k, v in raw_node.items() if k != "text"},
                    "doc_id": doc_id,
                    "node_id": node_id,
                    "chunk_id": chunk_id,
                    "modality": "text",
                    "hash": chunk_hash,
                    "text": chunk,
                    "embedding_text": _build_embedding_text(chunk, {**base_metadata, **raw_node}),
                }
                nodes.append(
                    TextNode(
                        node_id=node_id,
                        chunk_id=chunk_id,
                        doc_id=doc_id,
                        text=chunk,
                        metadata=metadata,
                    )
                )
            continue

        chunk_units: list[tuple[str, dict[str, Any]]] = []
        if page_map:
            for entry in page_map:
                page = entry.get("page")
                text = str(entry.get("text") or "").strip()
                if not text:
                    continue
                page_chunks = _split_generic_text(text, splitter, chunk_size=chunk_size, min_chars=min_chunk_chars)
                for chunk in page_chunks:
                    chunk_units.append((chunk, {"page": page}))
        else:
            generic_chunks = _split_generic_text(
                doc.text or doc.get_content(),
                splitter,
                chunk_size=chunk_size,
                min_chars=min_chunk_chars,
            )
            for chunk in generic_chunks:
                chunk_units.append((chunk, {}))

        for idx, (chunk, chunk_metadata) in enumerate(chunk_units):
            chunk_hash = _hash_text(chunk)
            node_id = f"{doc_id}::n{idx}::{chunk_hash[:8]}"
            chunk_id = f"{doc_id}::c{idx}"
            metadata = {
                **base_metadata,
                **chunk_metadata,
                "doc_id": doc_id,
                "node_id": node_id,
                "chunk_id": chunk_id,
                "modality": "text",
                "hash": chunk_hash,
                "text": chunk,
                "embedding_text": _build_embedding_text(chunk, {**base_metadata, **chunk_metadata}),
            }
            nodes.append(TextNode(node_id=node_id, chunk_id=chunk_id, doc_id=doc_id, text=chunk, metadata=metadata))
    return nodes
