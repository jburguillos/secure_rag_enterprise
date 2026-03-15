from __future__ import annotations

from llama_index.core import Document

from app.ingestion.parser import chunk_documents


def test_chunk_documents_preserves_pdf_page_metadata_and_embedding_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ingestion.parser.get_yaml_config",
        lambda: {"chunking": {"chunk_size": 120, "chunk_overlap": 20, "min_chunk_chars": 40}},
    )

    doc = Document(
        text="ignored because page_map is present",
        id_="doc-1",
        metadata={
            "doc_id": "doc-1",
            "title": "VCPaper.pdf",
            "mimeType": "application/pdf",
            "page_map": [
                {"page": 1, "text": "Venture capital research relied on structured datasets and early prospectus disclosures."},
                {"page": 2, "text": "IPO prospectuses were one of several information sources used in early empirical work."},
            ],
        },
    )

    nodes = chunk_documents([doc])

    assert len(nodes) >= 2
    assert {node.metadata.get("page") for node in nodes} == {1, 2}
    assert all("page_map" not in node.metadata for node in nodes)
    assert all("title: VCPaper.pdf" in str(node.metadata.get("embedding_text")) for node in nodes)
    assert any("page: 1" in str(node.metadata.get("embedding_text")) for node in nodes)
    assert any("page: 2" in str(node.metadata.get("embedding_text")) for node in nodes)


def test_chunk_documents_merges_tiny_trailing_chunks(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ingestion.parser.get_yaml_config",
        lambda: {"chunking": {"chunk_size": 120, "chunk_overlap": 20, "min_chunk_chars": 50}},
    )

    doc = Document(
        text="Paragraph one has enough detail to stand on its own.\n\nTiny tail.",
        id_="doc-2",
        metadata={"doc_id": "doc-2", "title": "tail.txt", "mimeType": "text/plain"},
    )

    nodes = chunk_documents([doc])

    assert len(nodes) == 1
    assert "Tiny tail." in nodes[0].text
