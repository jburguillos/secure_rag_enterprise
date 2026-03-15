from __future__ import annotations

from app.db.database import get_session
from app.db.init_db import init_db
from app.db.models import DocumentRecord
from app.db.repository import _json_safe, upsert_document


def test_json_safe_strips_null_bytes_from_nested_values() -> None:
    payload = {
        "title": "Board\x00Pack",
        "items": ["alpha\x00beta", {"note": "row\x00value"}],
        "headers": ("region", "reven\x00ue"),
    }

    sanitized = _json_safe(payload)

    assert sanitized["title"] == "BoardPack"
    assert sanitized["items"][0] == "alphabeta"
    assert sanitized["items"][1]["note"] == "rowvalue"
    assert sanitized["headers"][1] == "revenue"


def test_upsert_document_strips_null_bytes_from_metadata_and_permissions() -> None:
    init_db()

    with get_session() as session:
        upsert_document(
            session,
            doc_id="doc-\x001",
            source="google\x00_drive",
            title="Quarterly\x00 Pack",
            mime_type="application/pdf",
            modified_time=None,
            content_hash="hash\x00value",
            permissions_summary={"allowed_emails": ["jb\x00@example.com"]},
            metadata={
                "doc_id": "doc-\x001",
                "preview": "At = A0 \x00 e^rt",
                "nested": {"sheet_name": "Revenue\x00Sheet"},
            },
        )

        session.flush()
        record = session.query(DocumentRecord).filter(DocumentRecord.doc_id == "doc-1").one()
        assert record is not None
        assert record.doc_id == "doc-1"
        assert record.source == "google_drive"
        assert record.title == "Quarterly Pack"
        assert record.content_hash == "hashvalue"
        assert record.permissions_summary["allowed_emails"] == ["jb@example.com"]
        assert record.meta_json["preview"] == "At = A0  e^rt"
        assert record.meta_json["nested"]["sheet_name"] == "RevenueSheet"
