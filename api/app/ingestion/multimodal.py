"""Multimodal extraction helpers (PDF page renders + embedded images + optional OCR)."""

from __future__ import annotations

import hashlib
import io
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


_USEFUL_TEXT_MIN_CHARS = 40
_USEFUL_TEXT_MIN_TOKENS = 6
_PAGE_TEXT_MAX_CHARS = 700


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_doc_id(doc_id: str) -> str:
    value = doc_id.replace("\\", "_").replace("/", "_").replace(":", "_")
    return value


def _ocr_text(image_bytes: bytes, enable_ocr: bool) -> str:
    if not enable_ocr:
        return ""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return str(pytesseract.image_to_string(img) or "").strip()
    except Exception:
        return ""


def _normalized_text(value: str, *, max_chars: int | None = None) -> str:
    clean = " ".join(str(value or "").split()).strip()
    if max_chars and len(clean) > max_chars:
        return clean[:max_chars].rstrip()
    return clean


def _has_useful_text(value: str) -> bool:
    clean = _normalized_text(value)
    if len(clean) < _USEFUL_TEXT_MIN_CHARS:
        return False
    return len([token for token in clean.split(" ") if token]) >= _USEFUL_TEXT_MIN_TOKENS


def _embedding_text(*, ocr_text: str, fallback_text: str, doc_id: str, page: int, image_kind: str) -> tuple[str, str]:
    clean_ocr = _normalized_text(ocr_text)
    if _has_useful_text(clean_ocr):
        return clean_ocr, "ocr"

    clean_fallback = _normalized_text(fallback_text, max_chars=_PAGE_TEXT_MAX_CHARS)
    if clean_fallback:
        return clean_fallback, "page_text"

    return f"visual evidence from document {doc_id} page {page} ({image_kind})", "placeholder"


def _build_image_node(
    *,
    doc_id: str,
    page: int,
    image_kind: str,
    image_path: Path,
    image_hash: str,
    ocr_text: str,
    fallback_text: str,
    base_metadata: dict[str, Any],
    embedded_index: int | None = None,
) -> ImageNode:
    suffix = f"::embedded::{embedded_index}" if embedded_index is not None else "::page"
    node_id = f"{doc_id}::img::{page}{suffix}::{image_hash[:8]}"
    chunk_id = f"{doc_id}::img::{page}{suffix}"
    embedding_text, visual_text_source = _embedding_text(
        ocr_text=ocr_text,
        fallback_text=fallback_text,
        doc_id=doc_id,
        page=page,
        image_kind=image_kind,
    )
    clean_ocr = _normalized_text(ocr_text)
    fallback_preview = _normalized_text(fallback_text, max_chars=_PAGE_TEXT_MAX_CHARS)

    metadata = {
        **base_metadata,
        "doc_id": doc_id,
        "node_id": node_id,
        "chunk_id": chunk_id,
        "page": page,
        "modality": "image",
        "image_kind": image_kind,
        "embedded_index": embedded_index,
        "image_path": str(image_path),
        "hash": image_hash,
        "ocr_text": clean_ocr,
        "ocr_char_count": len(clean_ocr),
        "ocr_token_count": len([token for token in clean_ocr.split(" ") if token]),
        "has_useful_ocr": _has_useful_text(clean_ocr),
        "visual_text_source": visual_text_source,
        "page_text_preview": fallback_preview,
        "text": embedding_text,
    }

    return ImageNode(
        node_id=node_id,
        doc_id=doc_id,
        page=page,
        image_path=str(image_path),
        metadata=metadata,
    )


def _extract_embedded_images(
    *,
    pdf: fitz.Document,
    page: fitz.Page,
    page_idx: int,
    doc_id: str,
    output_dir: Path,
    base_metadata: dict[str, Any],
    enable_ocr: bool,
    seen_hashes: set[str],
    page_text: str,
) -> list[ImageNode]:
    nodes: list[ImageNode] = []
    for embedded_idx, image_ref in enumerate(page.get_images(full=True), start=1):
        xref = int(image_ref[0])
        image_info = pdf.extract_image(xref)
        image_bytes = image_info.get("image")
        if not image_bytes:
            continue

        image_hash = _hash_bytes(image_bytes)
        if image_hash in seen_hashes:
            continue
        seen_hashes.add(image_hash)

        ext = str(image_info.get("ext") or "png").lower()
        image_path = output_dir / f"page_{page_idx:04d}_embedded_{embedded_idx:03d}.{ext}"
        image_path.write_bytes(image_bytes)

        ocr_text = _ocr_text(image_bytes, enable_ocr=enable_ocr)
        nodes.append(
            _build_image_node(
                doc_id=doc_id,
                page=page_idx,
                image_kind="embedded",
                image_path=image_path,
                image_hash=image_hash,
                ocr_text=ocr_text,
                fallback_text=page_text,
                base_metadata=base_metadata,
                embedded_index=embedded_idx,
            )
        )
    return nodes


def _extract_pdf_images(
    *,
    pdf: fitz.Document,
    output_dir: Path,
    doc_id: str,
    base_metadata: dict[str, Any],
    enable_ocr: bool,
    include_embedded: bool,
) -> list[ImageNode]:
    nodes: list[ImageNode] = []
    seen_embedded_hashes: set[str] = set()

    for page_idx, page in enumerate(pdf, start=1):
        page_text = _normalized_text(page.get_text("text"), max_chars=_PAGE_TEXT_MAX_CHARS)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        page_image_bytes = pix.tobytes("png")
        page_hash = _hash_bytes(page_image_bytes)
        page_path = output_dir / f"page_{page_idx:04d}.png"
        page_path.write_bytes(page_image_bytes)

        nodes.append(
            _build_image_node(
                doc_id=doc_id,
                page=page_idx,
                image_kind="page",
                image_path=page_path,
                image_hash=page_hash,
                ocr_text=_ocr_text(page_image_bytes, enable_ocr=enable_ocr),
                fallback_text=page_text,
                base_metadata=base_metadata,
            )
        )

        if include_embedded:
            nodes.extend(
                _extract_embedded_images(
                    pdf=pdf,
                    page=page,
                    page_idx=page_idx,
                    doc_id=doc_id,
                    output_dir=output_dir,
                    base_metadata=base_metadata,
                    enable_ocr=enable_ocr,
                    seen_hashes=seen_embedded_hashes,
                    page_text=page_text,
                )
            )

    return nodes


def extract_pdf_page_images(
    *,
    pdf_path: str,
    output_root: str,
    doc_id: str,
    base_metadata: dict[str, Any],
    enable_ocr: bool,
    include_embedded: bool = True,
) -> list[ImageNode]:
    source = Path(pdf_path)
    if not source.exists():
        return []

    output_dir = Path(output_root) / _safe_doc_id(doc_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(str(source)) as pdf:
        return _extract_pdf_images(
            pdf=pdf,
            output_dir=output_dir,
            doc_id=doc_id,
            base_metadata=base_metadata,
            enable_ocr=enable_ocr,
            include_embedded=include_embedded,
        )


def extract_pdf_page_images_from_bytes(
    *,
    pdf_bytes: bytes,
    output_root: str,
    doc_id: str,
    base_metadata: dict[str, Any],
    enable_ocr: bool,
    include_embedded: bool = True,
) -> list[ImageNode]:
    if not pdf_bytes:
        return []

    output_dir = Path(output_root) / _safe_doc_id(doc_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return _extract_pdf_images(
            pdf=pdf,
            output_dir=output_dir,
            doc_id=doc_id,
            base_metadata=base_metadata,
            enable_ocr=enable_ocr,
            include_embedded=include_embedded,
        )
