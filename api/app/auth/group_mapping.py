"""Helpers to map IdP groups to Drive ACL groups."""

from __future__ import annotations

import json

from app.auth.context import Entitlements
from app.config import get_settings


def _normalize_group(value: str) -> str:
    return value.strip().lower().lstrip("/")


def _parse_group_map(raw: str) -> dict[str, list[str]]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    parsed: dict[str, list[str]] = {}
    for key, value in data.items():
        norm_key = _normalize_group(str(key))
        if not norm_key:
            continue

        mapped_values: list[str] = []
        if isinstance(value, str):
            mapped_values = [_normalize_group(value)]
        elif isinstance(value, list):
            mapped_values = [_normalize_group(str(item)) for item in value]

        cleaned = [item for item in mapped_values if item]
        if cleaned:
            parsed[norm_key] = sorted(set(cleaned))
    return parsed


def apply_drive_group_mapping(entitlements: Entitlements) -> Entitlements:
    """Return entitlements with mapped Drive groups appended."""

    settings = get_settings()
    mapping = _parse_group_map(settings.drive_group_map_json)
    if not mapping:
        return entitlements

    normalized_groups = {_normalize_group(g) for g in entitlements.groups if g}
    expanded = set(normalized_groups)
    for group in normalized_groups:
        expanded.update(mapping.get(group, []))

    return Entitlements(
        authenticated=entitlements.authenticated,
        user_id=entitlements.user_id,
        email=entitlements.email,
        domain=entitlements.domain,
        groups=sorted(expanded),
        allowed_users=entitlements.allowed_users,
        allowed_groups=entitlements.allowed_groups,
    )
