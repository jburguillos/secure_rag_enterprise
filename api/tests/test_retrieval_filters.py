from __future__ import annotations

from datetime import datetime, timezone

from qdrant_client.models import FieldCondition, Filter

from app.auth.context import Entitlements
from app.models.schemas import QueryFilters
from app.retrieval.acl import build_acl_filter
from app.retrieval.filters import build_metadata_filter, combine_filters


def test_build_metadata_filter_none() -> None:
    assert build_metadata_filter(None) is None


def test_build_metadata_filter_with_fields() -> None:
    filt = QueryFilters(
        sources=["google_drive"],
        mime_types=["application/pdf"],
        doc_ids=["doc-123"],
        tags=["finance"],
        folder_prefixes=["03_Portfolio/CliniFlow"],
        path_prefixes=["03_Portfolio/CliniFlow/Reporting"],
        modified_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_to=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

    out = build_metadata_filter(filt)
    assert out is not None

    must = list(out.must or [])
    keys = [item.key for item in must if isinstance(item, FieldCondition)]

    assert "source" in keys
    assert "mimeType" in keys
    assert "doc_id" in keys
    assert "modifiedTime" in keys
    should_filters = [item for item in must if isinstance(item, Filter) and item.should]
    assert len(should_filters) >= 3


def test_combine_filters_wraps_acl_and_metadata() -> None:
    ent = Entitlements(authenticated=True, email="alice@example.com", domain="example.com")
    acl = build_acl_filter(ent)
    md = build_metadata_filter(QueryFilters(sources=["google_drive"]))

    combined = combine_filters(acl, md)
    assert combined.must is not None
    assert len(combined.must) == 2
