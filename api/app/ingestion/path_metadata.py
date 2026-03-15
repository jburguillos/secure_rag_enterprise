"""Path normalization helpers for ingestion metadata."""

from __future__ import annotations

import re


def normalize_path(value: str | None) -> str:
    """Return a normalized slash-separated path without leading/trailing separators."""

    if not value:
        return ""
    cleaned = str(value).replace("\\", "/").strip()
    cleaned = re.sub(r"/{2,}", "/", cleaned).strip("/")
    return cleaned


def path_ancestors(value: str | None) -> list[str]:
    """Return lowercase cumulative ancestors for exact-match prefix filtering."""

    cleaned = normalize_path(value).lower()
    if not cleaned:
        return []
    parts = [part for part in cleaned.split("/") if part]
    return ["/".join(parts[:idx]) for idx in range(1, len(parts) + 1)]

