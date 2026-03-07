from __future__ import annotations

from pathlib import Path

from app.ingestion.local_connector import load_local_documents


def test_local_ingestion_acl_sidecar(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "sample.txt").write_text("hello secure rag", encoding="utf-8")

    acl_path = tmp_path / "acl.yaml"
    acl_path.write_text(
        "documents:\n  sample.txt:\n    is_public: false\n    allowed_domains: [corp.com]\n",
        encoding="utf-8",
    )

    docs, skipped = load_local_documents(str(docs_dir), str(acl_path))
    assert not skipped
    assert len(docs) == 1
    md = docs[0].metadata or {}
    assert md.get("allowed_domains") == ["corp.com"]
    assert md.get("is_public") is False
