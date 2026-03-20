from __future__ import annotations

import pytest

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
