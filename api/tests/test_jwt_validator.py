from __future__ import annotations

import pytest

from app.auth.jwt_validator import JWTValidator


@pytest.mark.asyncio
async def test_validate_accepts_any_allowed_audience(monkeypatch) -> None:
    validator = JWTValidator(
        issuer="http://keycloak:8080/realms/secure-rag",
        audience="secure-rag-api,account",
        allowed_issuers=["http://localhost:8080/realms/secure-rag"],
    )

    async def _fake_fetch_jwks():
        return {"keys": []}

    def _fake_decode(*args, **kwargs):
        return {
            "iss": "http://localhost:8080/realms/secure-rag",
            "aud": "account",
            "sub": "u-1",
        }

    monkeypatch.setattr(validator, "_fetch_jwks", _fake_fetch_jwks)
    monkeypatch.setattr("app.auth.jwt_validator.jwt.decode", _fake_decode)

    claims = await validator.validate("dummy-token")
    assert claims["aud"] == "account"


@pytest.mark.asyncio
async def test_validate_rejects_disallowed_audience(monkeypatch) -> None:
    validator = JWTValidator(
        issuer="http://keycloak:8080/realms/secure-rag",
        audience="secure-rag-api",
        allowed_issuers=["http://localhost:8080/realms/secure-rag"],
    )

    async def _fake_fetch_jwks():
        return {"keys": []}

    def _fake_decode(*args, **kwargs):
        return {
            "iss": "http://localhost:8080/realms/secure-rag",
            "aud": "account",
            "sub": "u-1",
        }

    monkeypatch.setattr(validator, "_fetch_jwks", _fake_fetch_jwks)
    monkeypatch.setattr("app.auth.jwt_validator.jwt.decode", _fake_decode)

    with pytest.raises(ValueError, match="audience_mismatch"):
        await validator.validate("dummy-token")
