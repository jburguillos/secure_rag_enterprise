"""JWT validation against Keycloak OIDC metadata."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from jose import jwt


@dataclass
class JWTValidator:
    issuer: str
    audience: str
    timeout_seconds: float = 5.0
    allowed_issuers: list[str] | None = None

    _jwks: dict[str, Any] | None = None
    _jwks_fetched_at: float = 0.0

    def _normalized_issuers(self) -> set[str]:
        issuers = {self.issuer.rstrip("/").lower()}
        for alias in self.allowed_issuers or []:
            if alias:
                issuers.add(alias.rstrip("/").lower())
        return issuers

    async def _fetch_jwks(self) -> dict[str, Any]:
        now = time.time()
        if self._jwks and now - self._jwks_fetched_at < 600:
            return self._jwks

        certs_url = self.issuer.rstrip("/") + "/protocol/openid-connect/certs"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(certs_url)
            response.raise_for_status()
            jwks = response.json()

        self._jwks = jwks
        self._jwks_fetched_at = now
        return jwks

    async def validate(self, token: str) -> dict[str, Any]:
        jwks = await self._fetch_jwks()
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256", "RS384", "RS512"],
            audience=self.audience,
            options={"verify_aud": True, "verify_exp": True, "verify_iss": False},
        )

        token_issuer = str(claims.get("iss") or "").rstrip("/").lower()
        if token_issuer not in self._normalized_issuers():
            raise ValueError("issuer_mismatch")
        return claims
