from __future__ import annotations

import pytest

from app.auth.context import Entitlements
from app.auth.service import resolve_entitlements


@pytest.mark.asyncio
async def test_transitional_context_when_auth_disabled() -> None:
    ent = await resolve_entitlements(
        authorization_header=None,
        transitional_context={"email": "hr.user@example.com", "groups": ["HR"]},
    )
    assert ent.email == "hr.user@example.com"
    assert "hr" in ent.groups


@pytest.mark.asyncio
async def test_transitional_group_mapping_to_drive_group(monkeypatch) -> None:
    monkeypatch.setenv("DRIVE_GROUP_MAP_JSON", '{"hr":["hr-shared@enterprise.com"]}')

    ent = await resolve_entitlements(
        authorization_header=None,
        transitional_context={"email": "hr.user@example.com", "groups": ["HR"]},
    )

    assert "hr" in ent.groups
    assert "hr-shared@enterprise.com" in ent.groups


@pytest.mark.asyncio
async def test_claim_group_mapping_to_drive_group(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("DRIVE_GROUP_MAP_JSON", '{"finance":["finance-shared@enterprise.com"]}')

    async def _fake_validate(self, token: str) -> dict:
        assert token == "abc"
        return {
            "sub": "u-1",
            "email": "finance.user@example.com",
            "groups": ["/Finance"],
        }

    monkeypatch.setattr("app.auth.jwt_validator.JWTValidator.validate", _fake_validate)

    ent = await resolve_entitlements(
        authorization_header="Bearer abc",
        transitional_context=None,
    )

    assert "finance" in ent.groups
    assert "finance-shared@enterprise.com" in ent.groups


@pytest.mark.asyncio
async def test_claims_enriched_from_userinfo_when_missing_email_and_groups(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")

    async def _fake_validate(self, token: str) -> dict:
        assert token == "abc"
        return {
            "sub": "u-1",
            "preferred_username": "jburguillos.drive",
        }

    async def _fake_userinfo(*, issuer: str, token: str) -> dict:
        assert token == "abc"
        assert issuer
        return {
            "email": "jburguillos.ieu2021@student.ieu.edu",
            "groups": ["/HR"],
            "preferred_username": "jburguillos.drive",
        }

    monkeypatch.setattr("app.auth.jwt_validator.JWTValidator.validate", _fake_validate)
    monkeypatch.setattr("app.auth.service._fetch_userinfo", _fake_userinfo)

    ent = await resolve_entitlements(
        authorization_header="Bearer abc",
        transitional_context=None,
    )

    assert ent.email == "jburguillos.ieu2021@student.ieu.edu"
    assert "hr" in ent.groups


@pytest.mark.asyncio
async def test_userinfo_not_called_when_claims_are_complete(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")

    async def _fake_validate(self, token: str) -> dict:
        assert token == "abc"
        return {
            "sub": "u-1",
            "email": "finance.user@example.com",
            "groups": ["/Finance"],
        }

    async def _boom_userinfo(*, issuer: str, token: str) -> dict:  # pragma: no cover - should not be reached
        raise AssertionError("userinfo should not be called")

    monkeypatch.setattr("app.auth.jwt_validator.JWTValidator.validate", _fake_validate)
    monkeypatch.setattr("app.auth.service._fetch_userinfo", _boom_userinfo)

    ent = await resolve_entitlements(
        authorization_header="Bearer abc",
        transitional_context=None,
    )

    assert ent.email == "finance.user@example.com"
    assert "finance" in ent.groups


def test_entitlements_from_claims_falls_back_to_upn_email() -> None:
    ent = Entitlements.from_claims(
        {
            "sub": "u-1",
            "upn": "person@corp.example",
            "groups": ["/HR"],
        }
    )
    assert ent.email == "person@corp.example"
    assert ent.domain == "corp.example"
    assert "hr" in ent.groups


@pytest.mark.asyncio
async def test_userinfo_tries_token_issuer_then_keycloak_then_aliases(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", "http://keycloak:8080/realms/secure-rag")
    monkeypatch.setenv(
        "KEYCLOAK_ISSUER_ALIASES",
        "http://localhost:8080/realms/secure-rag,http://172.205.216.237/realms/secure-rag",
    )

    async def _fake_validate(self, token: str) -> dict:
        assert token == "abc"
        return {
            "sub": "u-1",
            "iss": "http://172.205.216.237/realms/secure-rag",
        }

    calls: list[str] = []

    async def _fake_userinfo(*, issuer: str, token: str):
        assert token == "abc"
        calls.append(issuer.rstrip("/"))
        if issuer.rstrip("/") == "http://172.205.216.237/realms/secure-rag":
            return {
                "email": "jburguillos.ieu2021@student.ieu.edu",
                "groups": ["/HR"],
            }
        return None

    monkeypatch.setattr("app.auth.jwt_validator.JWTValidator.validate", _fake_validate)
    monkeypatch.setattr("app.auth.service._fetch_userinfo", _fake_userinfo)

    ent = await resolve_entitlements(
        authorization_header="Bearer abc",
        transitional_context=None,
    )

    assert calls[0] == "http://172.205.216.237/realms/secure-rag"
    assert ent.email == "jburguillos.ieu2021@student.ieu.edu"
    assert "hr" in ent.groups
