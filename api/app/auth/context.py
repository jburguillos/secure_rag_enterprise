"""Authentication context models."""

from __future__ import annotations

from dataclasses import dataclass, field


def _normalize_group(value: str) -> str:
    return value.strip().lower().lstrip("/")


@dataclass
class Entitlements:
    authenticated: bool = False
    user_id: str | None = None
    email: str | None = None
    domain: str | None = None
    groups: list[str] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)
    allowed_groups: list[str] = field(default_factory=list)

    @classmethod
    def from_claims(cls, claims: dict) -> "Entitlements":
        email = (claims.get("email") or "").lower() or None
        domain = email.split("@", 1)[1] if email and "@" in email else None

        raw_groups = [str(g) for g in (claims.get("groups") or []) if g]
        # Fallback for realms/clients that expose role claims instead of groups.
        if not raw_groups:
            realm_roles = ((claims.get("realm_access") or {}).get("roles") or [])
            raw_groups.extend(str(role) for role in realm_roles if role)

        groups = sorted({_normalize_group(g) for g in raw_groups if _normalize_group(g)})
        user_id = claims.get("sub") or claims.get("preferred_username")
        return cls(
            authenticated=True,
            user_id=str(user_id) if user_id else None,
            email=email,
            domain=domain,
            groups=groups,
        )

    @classmethod
    def from_transitional(cls, ctx: dict | None) -> "Entitlements":
        if not ctx:
            return cls(authenticated=False)
        email = (ctx.get("email") or "").lower() or None
        domain = ctx.get("domain")
        if not domain and email and "@" in email:
            domain = email.split("@", 1)[1]
        groups = [str(g).lower() for g in (ctx.get("groups") or [])]
        user_id = ctx.get("user_id")
        return cls(
            authenticated=bool(email or user_id),
            user_id=user_id,
            email=email,
            domain=domain.lower() if isinstance(domain, str) else None,
            groups=groups,
            allowed_users=[str(u).lower() for u in (ctx.get("allowed_users") or [])],
            allowed_groups=[str(g).lower() for g in (ctx.get("allowed_groups") or [])],
        )
