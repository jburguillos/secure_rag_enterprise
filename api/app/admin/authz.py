"""Admin authorization helpers."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from app.auth.context import Entitlements
from app.auth.service import resolve_entitlements
from app.config import get_settings


def _normalize_group(value: str) -> str:
    return value.strip().lower().lstrip("/")


def parse_admin_groups(raw: str) -> set[str]:
    groups = {_normalize_group(item) for item in raw.split(",") if item.strip()}
    return {group for group in groups if group}


def has_admin_role(entitlements: Entitlements, allowed_groups: set[str]) -> bool:
    if not allowed_groups:
        return False
    user_groups = {_normalize_group(group) for group in entitlements.groups if group}
    return bool(user_groups.intersection(allowed_groups))


async def require_admin_entitlements(authorization: str | None = Header(default=None)) -> Entitlements:
    settings = get_settings()
    if not settings.auth_enabled:
        raise HTTPException(status_code=403, detail="Admin endpoints require AUTH_ENABLED=true")

    entitlements = await resolve_entitlements(
        authorization_header=authorization,
        transitional_context=None,
    )

    allowed_groups = parse_admin_groups(settings.admin_authorized_groups)
    if not has_admin_role(entitlements, allowed_groups):
        raise HTTPException(status_code=403, detail="Admin role required")

    return entitlements


def require_admin_dependency() -> Depends:
    return Depends(require_admin_entitlements)
