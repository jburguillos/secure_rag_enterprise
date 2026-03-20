"""Metadata filter helpers for retrieval."""

from __future__ import annotations

from datetime import timezone
import re

from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchAny, MatchValue

from app.models.schemas import QueryFilters


def _normalize_many(values: list[str] | None) -> list[str]:
    return sorted({v.strip() for v in (values or []) if v and v.strip()})


def _normalize_path(value: str | None) -> str:
    if not value:
        return ""
    cleaned = str(value).replace("\\", "/").strip()
    cleaned = re.sub(r"/{2,}", "/", cleaned).strip("/")
    return cleaned


def _normalize_path_many(values: list[str] | None) -> list[str]:
    normalized: set[str] = set()
    for raw in values or []:
        cleaned = _normalize_path(raw)
        if not cleaned:
            continue
        normalized.add(cleaned)
        normalized.add(cleaned.lower())
    return sorted(normalized)


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
        # Backward-compatible MIME filtering:
        # some indexed nodes use `mimeType` while others expose `type`.
        must.append(
            Filter(
                should=[
                    FieldCondition(key="mimeType", match=MatchAny(any=mime_types)),
                    FieldCondition(key="type", match=MatchAny(any=mime_types)),
                ]
            )
        )

    doc_ids = _normalize_many(filters.doc_ids)
    if doc_ids:
        must.append(FieldCondition(key="doc_id", match=MatchAny(any=doc_ids)))

    tags = _normalize_many(filters.tags)
    if tags:
        tag_should = [FieldCondition(key="tags", match=MatchValue(value=tag)) for tag in tags]
        must.append(Filter(should=tag_should))

    folder_prefixes = _normalize_path_many(filters.folder_prefixes)
    if folder_prefixes:
        folder_prefixes_lower = sorted({prefix.lower() for prefix in folder_prefixes})
        must.append(
            Filter(
                should=[
                    FieldCondition(key="folder_ancestors", match=MatchAny(any=folder_prefixes_lower)),
                    FieldCondition(key="folder_path", match=MatchAny(any=folder_prefixes)),
                ]
            )
        )

    path_prefixes = _normalize_path_many(filters.path_prefixes)
    if path_prefixes:
        path_prefixes_lower = sorted({prefix.lower() for prefix in path_prefixes})
        must.append(
            Filter(
                should=[
                    FieldCondition(key="path_ancestors", match=MatchAny(any=path_prefixes_lower)),
                    FieldCondition(key="drive_path", match=MatchAny(any=path_prefixes)),
                    FieldCondition(key="source_path", match=MatchAny(any=path_prefixes)),
                ]
            )
        )

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
