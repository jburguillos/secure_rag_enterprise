from __future__ import annotations

import pytest

from app.auth.context import Entitlements
from app.policy.opa_client import PolicyClient


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DummyClient:
    def __init__(self, payload=None, fail=False):
        self.payload = payload
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, *_args, **_kwargs):
        if self.fail:
            raise RuntimeError("opa down")
        return _DummyResponse(self.payload)


@pytest.mark.asyncio
async def test_opa_allow(monkeypatch) -> None:
    monkeypatch.setattr("app.policy.opa_client.httpx.AsyncClient", lambda timeout=3.0: _DummyClient(payload={"result": {"allow": True, "reason": "ok", "policy_version": "1.0"}}))
    client = PolicyClient()
    ent = Entitlements(authenticated=True, email="hr.user@example.com", groups=["hr"])
    result = await client.evaluate(entitlements=ent, resource_acl={"is_public": True})
    assert result.allow
    assert result.reason == "ok"


@pytest.mark.asyncio
async def test_opa_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("OPA_FAIL_CLOSED", "true")
    monkeypatch.setattr("app.policy.opa_client.httpx.AsyncClient", lambda timeout=3.0: _DummyClient(fail=True))
    client = PolicyClient()
    ent = Entitlements(authenticated=True, email="hr.user@example.com", groups=["hr"])
    result = await client.evaluate(entitlements=ent, resource_acl={"is_public": False})
    assert not result.allow
    assert result.reason == "opa_unavailable_fail_closed"
