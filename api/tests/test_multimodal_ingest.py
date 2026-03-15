from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import fitz
from PIL import Image
from llama_index.core import Document

from app.ingestion.multimodal import extract_pdf_page_images, extract_pdf_page_images_from_bytes
from app.ingestion.pipeline import IngestionService


def _make_pdf_with_text_bytes(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    output = pdf.tobytes()
    pdf.close()
    return output


def _make_pdf_with_embedded_image_bytes() -> bytes:
    img = Image.new("RGB", (64, 64), color=(255, 0, 0))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Contains embedded image")
    page.insert_image(fitz.Rect(72, 120, 196, 244), stream=png_bytes)
    output = pdf.tobytes()
    pdf.close()
    return output


def test_pdf_page_image_extraction(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello multimodal")
    doc.save(pdf_path)
    doc.close()

    nodes = extract_pdf_page_images(
        pdf_path=str(pdf_path),
        output_root=str(tmp_path / "images"),
        doc_id="doc-1",
        base_metadata={"is_public": True},
        enable_ocr=False,
    )
    assert nodes
    assert Path(nodes[0].image_path).exists()
    assert any(node.metadata.get("image_kind") == "page" for node in nodes)
    assert any(node.metadata.get("visual_text_source") == "page_text" for node in nodes)
    assert any("Hello multimodal" in str(node.metadata.get("text") or "") for node in nodes)


def test_pdf_bytes_extraction_includes_embedded_images(tmp_path: Path) -> None:
    pdf_bytes = _make_pdf_with_embedded_image_bytes()

    nodes = extract_pdf_page_images_from_bytes(
        pdf_bytes=pdf_bytes,
        output_root=str(tmp_path / "images"),
        doc_id="doc-embedded",
        base_metadata={"is_public": True, "dataset_source": "google_drive"},
        enable_ocr=False,
    )

    assert nodes
    image_kinds = {str(node.metadata.get("image_kind")) for node in nodes}
    assert "page" in image_kinds
    assert "embedded" in image_kinds
    assert all(Path(node.image_path).exists() for node in nodes)


def test_drive_pdf_image_node_extraction_keeps_acl_metadata(tmp_path: Path, monkeypatch) -> None:
    pdf_bytes = _make_pdf_with_text_bytes("Drive PDF page")

    service = IngestionService.__new__(IngestionService)
    service.settings = SimpleNamespace(pdf_image_root=str(tmp_path / "images"), enable_ocr=False)

    docs = [
        Document(
            text="placeholder",
            metadata={
                "doc_id": "drive-file-1",
                "file_id": "drive-file-1",
                "mimeType": "application/pdf",
                "allowed_emails": ["hr.user@example.com"],
                "allowed_domains": ["example.com"],
                "is_public": False,
                "dataset_source": "google_drive",
            },
        ),
        Document(
            text="not a pdf",
            metadata={
                "doc_id": "drive-file-2",
                "file_id": "drive-file-2",
                "mimeType": "text/plain",
            },
        ),
    ]

    monkeypatch.setattr(
        "app.ingestion.pipeline.download_drive_file_bytes",
        lambda *, file_id, mime_type, service: pdf_bytes,
    )

    image_nodes, errors = IngestionService._extract_drive_pdf_image_nodes(service, docs, drive_service=object())

    assert errors == []
    assert image_nodes
    assert all(node.get("doc_id") == "drive-file-1" for node in image_nodes)
    assert all(node.get("dataset_source") == "google_drive" for node in image_nodes)
    assert all(node.get("allowed_domains") == ["example.com"] for node in image_nodes)
    assert any(node.get("image_kind") == "page" for node in image_nodes)


def test_image_nodes_are_linked_to_same_page_text_chunks() -> None:
    text_nodes = [
        SimpleNamespace(
            node_id="doc-1::n0",
            chunk_id="doc-1::c0",
            doc_id="doc-1",
            text="Text for page one",
            metadata={"page": 1},
        ),
        SimpleNamespace(
            node_id="doc-1::n1",
            chunk_id="doc-1::c1",
            doc_id="doc-1",
            text="Text for page two",
            metadata={"page": 2},
        ),
    ]
    image_nodes = [
        {"doc_id": "doc-1", "page": 2, "node_id": "doc-1::img::2", "text": "page text"},
        {"doc_id": "doc-1", "page": 3, "node_id": "doc-1::img::3", "text": "page text"},
    ]

    enriched = IngestionService._annotate_image_nodes_with_text_links(image_nodes, text_nodes)

    assert enriched[0]["linked_text_node_ids"] == ["doc-1::n1"]
    assert enriched[0]["linked_chunk_ids"] == ["doc-1::c1"]
    assert enriched[0]["linked_text_count"] == 1
    assert "Text for page two" in enriched[0]["linked_text_preview"]
    assert enriched[1]["linked_text_node_ids"] == []
    assert enriched[1]["linked_text_count"] == 0
