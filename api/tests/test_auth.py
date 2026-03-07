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
