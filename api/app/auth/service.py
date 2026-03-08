"""Auth service for deriving request entitlements."""

from __future__ import annotations

from fastapi import HTTPException

from app.auth.context import Entitlements
from app.auth.group_mapping import apply_drive_group_mapping
from app.auth.jwt_validator import JWTValidator
from app.config import get_settings


def _issuer_aliases(raw_aliases: str) -> list[str]:
    return [value.strip() for value in raw_aliases.split(",") if value.strip()]


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

    entitlements = Entitlements.from_claims(claims)
    return apply_drive_group_mapping(entitlements)
