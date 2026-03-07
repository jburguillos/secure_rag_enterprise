from __future__ import annotations

from pathlib import Path

import fitz

from app.ingestion.multimodal import extract_pdf_page_images


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
