"""Helpers to map IdP groups to Drive ACL groups."""

from __future__ import annotations

from app.admin.settings_store import get_effective_drive_group_map
from app.auth.context import Entitlements


def _normalize_group(value: str) -> str:
    return value.strip().lower().lstrip("/")


def apply_drive_group_mapping(
    entitlements: Entitlements,
    mapping_override: dict[str, list[str]] | None = None,
) -> Entitlements:
    """Return entitlements with mapped Drive groups appended."""

    mapping = mapping_override if mapping_override is not None else get_effective_drive_group_map()
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
