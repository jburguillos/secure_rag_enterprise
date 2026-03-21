"""Auth service for deriving request entitlements."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from app.auth.context import Entitlements
from app.auth.group_mapping import apply_drive_group_mapping
from app.auth.jwt_validator import JWTValidator
from app.config import get_settings


def _issuer_aliases(raw_aliases: str) -> list[str]:
    return [value.strip() for value in raw_aliases.split(",") if value.strip()]


def _claims_need_userinfo_enrichment(claims: dict[str, Any]) -> bool:
    has_email = bool(claims.get("email"))
    has_groups = bool(claims.get("groups")) or bool(((claims.get("realm_access") or {}).get("roles") or []))
    return not has_email or not has_groups


def _userinfo_issuers(*, token_issuer: str | None, keycloak_issuer: str, aliases_raw: str) -> list[str]:
    ordered: list[str] = []
    for candidate in [token_issuer, keycloak_issuer, *_issuer_aliases(aliases_raw)]:
        issuer = str(candidate or "").strip().rstrip("/")
        if not issuer:
            continue
        if issuer not in ordered:
            ordered.append(issuer)
    return ordered


async def _fetch_userinfo(*, issuer: str, token: str) -> dict[str, Any] | None:
    userinfo_url = issuer.rstrip("/") + "/protocol/openid-connect/userinfo"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _merge_claims_with_userinfo(claims: dict[str, Any], userinfo: dict[str, Any]) -> dict[str, Any]:
    merged = dict(claims)
    if not merged.get("email") and userinfo.get("email"):
        merged["email"] = userinfo.get("email")
    if not merged.get("preferred_username") and userinfo.get("preferred_username"):
        merged["preferred_username"] = userinfo.get("preferred_username")
    if not merged.get("sub") and userinfo.get("sub"):
        merged["sub"] = userinfo.get("sub")
    if not merged.get("groups") and userinfo.get("groups"):
        merged["groups"] = userinfo.get("groups")
    return merged


async def resolve_entitlements(
    *,
    authorization_header: str | None,
    transitional_context: dict | None,
) -> Entitlements:
    settings = get_settings()
    if not settings.auth_enabled:
        entitlements = Entitlements.from_transitional(transitional_context)
        return apply_drive_group_mapping(entitlements)

    if not authorization_header or not authorization_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization_header.split(" ", 1)[1].strip()
    validator = JWTValidator(
        issuer=settings.keycloak_issuer,
        audience=settings.keycloak_audience,
        allowed_issuers=[settings.keycloak_issuer, *_issuer_aliases(settings.keycloak_issuer_aliases)],
    )
    try:
        claims = await validator.validate(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if _claims_need_userinfo_enrichment(claims):
        token_issuer = str(claims.get("iss") or "")
        for issuer in _userinfo_issuers(
            token_issuer=token_issuer,
            keycloak_issuer=settings.keycloak_issuer,
            aliases_raw=settings.keycloak_issuer_aliases,
        ):
            userinfo = await _fetch_userinfo(issuer=issuer, token=token)
            if userinfo:
                claims = _merge_claims_with_userinfo(claims, userinfo)
                break

    entitlements = Entitlements.from_claims(claims)
    return apply_drive_group_mapping(entitlements)
