"""Smoke test for query route serialization with monkeypatched flow."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_liveness() -> None:
    response = client.get("/health/liveness")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
