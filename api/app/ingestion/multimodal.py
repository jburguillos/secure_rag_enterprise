"""Multimodal extraction helpers (PDF pages + optional OCR)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
from PIL import Image
import pytesseract


@dataclass
class ImageNode:
    node_id: str
    doc_id: str
    page: int
    image_path: str
    metadata: dict[str, Any]


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def extract_pdf_page_images(*, pdf_path: str, output_root: str, doc_id: str, base_metadata: dict[str, Any], enable_ocr: bool) -> list[ImageNode]:
    source = Path(pdf_path)
    if not source.exists():
        return []

    output_dir = Path(output_root) / doc_id.replace("/", "_")
    output_dir.mkdir(parents=True, exist_ok=True)

    nodes: list[ImageNode] = []
    with fitz.open(str(source)) as pdf:
        for page_idx, page in enumerate(pdf, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image_bytes = pix.tobytes("png")
            image_hash = _hash_bytes(image_bytes)
            node_id = f"{doc_id}::img::{page_idx}::{image_hash[:8]}"
            image_path = output_dir / f"page_{page_idx}.png"
            image_path.write_bytes(image_bytes)

            ocr_text = ""
            if enable_ocr:
                try:
                    with Image.open(image_path) as img:
                        ocr_text = pytesseract.image_to_string(img)
                except Exception:
                    ocr_text = ""

            metadata = {
                **base_metadata,
                "doc_id": doc_id,
                "node_id": node_id,
                "chunk_id": f"{doc_id}::img::{page_idx}",
                "page": page_idx,
                "modality": "image",
                "image_path": str(image_path),
                "hash": image_hash,
                "ocr_text": ocr_text,
                "text": ocr_text,
            }
            nodes.append(
                ImageNode(
                    node_id=node_id,
                    doc_id=doc_id,
                    page=page_idx,
                    image_path=str(image_path),
                    metadata=metadata,
                )
            )
    return nodes
