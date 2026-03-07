"""Document parsing and chunking utilities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
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


def chunk_documents(documents: list[Document]) -> list[TextNode]:
    cfg = get_yaml_config()
    chunking_cfg = cfg.get("chunking", {})
    splitter = SentenceSplitter(
        chunk_size=int(chunking_cfg.get("chunk_size", 800)),
        chunk_overlap=int(chunking_cfg.get("chunk_overlap", 120)),
    )

    nodes: list[TextNode] = []
    for doc in documents:
        doc_id = str((doc.metadata or {}).get("doc_id") or doc.id_)
        base_metadata = dict(doc.metadata or {})
        chunks = splitter.split_text(doc.text or doc.get_content())
        for idx, chunk in enumerate(chunks):
            chunk_hash = _hash_text(chunk)
            node_id = f"{doc_id}::n{idx}::{chunk_hash[:8]}"
            chunk_id = f"{doc_id}::c{idx}"
            metadata = {
                **base_metadata,
                "doc_id": doc_id,
                "node_id": node_id,
                "chunk_id": chunk_id,
                "modality": "text",
                "hash": chunk_hash,
                "text": chunk,
            }
            nodes.append(TextNode(node_id=node_id, chunk_id=chunk_id, doc_id=doc_id, text=chunk, metadata=metadata))
    return nodes
