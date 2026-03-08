from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.admin.authz import require_admin_entitlements
from app.auth.context import Entitlements
from app.db.database import get_session
from app.db.models import DocumentRecord
from app.main import app


def _admin_override() -> Entitlements:
    return Entitlements(authenticated=True, email="admin.user@example.com", groups=["admin"])


def test_admin_drive_mapping_set_get() -> None:
    app.dependency_overrides[require_admin_entitlements] = _admin_override
    try:
        with TestClient(app) as client:
            put_resp = client.put(
                "/admin/settings/drive-group-map",
                json={"mapping": {"hr": ["hr-shared@enterprise.com"]}},
            )
            assert put_resp.status_code == 200
            assert put_resp.json()["source"] == "db"

            get_resp = client.get("/admin/settings/drive-group-map")
            assert get_resp.status_code == 200
            body = get_resp.json()
            assert body["source"] == "db"
            assert body["mapping"]["hr"] == ["hr-shared@enterprise.com"]
    finally:
        app.dependency_overrides.clear()


def test_admin_access_preview_applies_acl() -> None:
    app.dependency_overrides[require_admin_entitlements] = _admin_override
    try:
        with TestClient(app) as client:
            now = datetime.now(timezone.utc)
            with get_session() as session:
                session.execute(delete(DocumentRecord).where(DocumentRecord.doc_id.in_(["admin_preview_public", "admin_preview_finance"])))
                session.add(
                    DocumentRecord(
                        doc_id="admin_preview_public",
                        source="local_folder",
                        title="Public",
                        mime_type="text/plain",
                        modified_time=now,
                        permissions_summary={"is_public": True},
                        meta_json={},
                    )
                )
                session.add(
                    DocumentRecord(
                        doc_id="admin_preview_finance",
                        source="local_folder",
                        title="Finance",
                        mime_type="text/plain",
                        modified_time=now,
                        permissions_summary={"is_public": False, "allowed_groups": ["finance"]},
                        meta_json={},
                    )
                )

            resp = client.post(
                "/admin/access/preview",
                json={
                    "principal": {
                        "email": "finance.user@example.com",
                        "domain": "example.com",
                        "groups": ["finance"],
                    },
                    "sources": ["local_folder"],
                    "limit": 50,
                },
            )

            assert resp.status_code == 200
            payload = resp.json()
            ids = {row["doc_id"] for row in payload["documents"]}
            assert "admin_preview_public" in ids
            assert "admin_preview_finance" in ids
    finally:
        app.dependency_overrides.clear()


def test_admin_keycloak_users_route_uses_client(monkeypatch) -> None:
    app.dependency_overrides[require_admin_entitlements] = _admin_override

    class _DummyClient:
        async def list_users(self, *, search=None, max_users=100):
            return [
                SimpleNamespace(
                    id="u-1",
                    username="alice",
                    email="alice@example.com",
                    enabled=True,
                    first_name="Alice",
                    last_name="Admin",
                )
            ]

        async def list_groups(self):
            return [SimpleNamespace(id="g-1", name="Admin", path="/Admin")]

        async def create_user(self, **_kwargs):
            return "u-2", ["Admin"]

        async def set_user_groups(self, *, user_id: str, groups: list[str]):
            return groups

    monkeypatch.setattr("app.api.admin.KeycloakAdminClient", _DummyClient)

    try:
        with TestClient(app) as client:
            resp = client.get("/admin/keycloak/users")
            assert resp.status_code == 200
            body = resp.json()
            assert body["users"][0]["username"] == "alice"
    finally:
        app.dependency_overrides.clear()
