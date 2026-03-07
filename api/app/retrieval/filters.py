"""Metadata filter helpers for retrieval."""

from __future__ import annotations

from datetime import timezone

from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchAny, MatchValue

from app.models.schemas import QueryFilters


def _normalize_many(values: list[str] | None) -> list[str]:
    return sorted({v.strip() for v in (values or []) if v and v.strip()})


def build_metadata_filter(filters: QueryFilters | None) -> Filter | None:
    """Build optional metadata filter for retrieval queries."""

    if filters is None:
        return None

    must: list[FieldCondition | Filter] = []

    sources = _normalize_many(filters.sources)
    if sources:
        must.append(FieldCondition(key="source", match=MatchAny(any=sources)))

    mime_types = _normalize_many(filters.mime_types)
    if mime_types:
        must.append(FieldCondition(key="mimeType", match=MatchAny(any=mime_types)))

    doc_ids = _normalize_many(filters.doc_ids)
    if doc_ids:
        must.append(FieldCondition(key="doc_id", match=MatchAny(any=doc_ids)))

    tags = _normalize_many(filters.tags)
    if tags:
        tag_should = [FieldCondition(key="tags", match=MatchValue(value=tag)) for tag in tags]
        must.append(Filter(should=tag_should))

    modified_from = filters.modified_from
    modified_to = filters.modified_to
    if modified_from or modified_to:
        if modified_from and modified_from.tzinfo is None:
            modified_from = modified_from.replace(tzinfo=timezone.utc)
        if modified_to and modified_to.tzinfo is None:
            modified_to = modified_to.replace(tzinfo=timezone.utc)
        must.append(
            FieldCondition(
                key="modifiedTime",
                range=DatetimeRange(gte=modified_from, lte=modified_to),
            )
        )

    if not must:
        return None
    return Filter(must=must)


def combine_filters(primary: Filter, secondary: Filter | None) -> Filter:
    """Combine ACL filter with optional metadata filter."""

    if secondary is None:
        return primary
    return Filter(must=[primary, secondary])
