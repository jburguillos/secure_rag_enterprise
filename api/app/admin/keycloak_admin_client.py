"""Minimal Keycloak Admin API client for operational user/group management."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import get_settings


class KeycloakAdminError(RuntimeError):
    """Raised when Keycloak admin operations fail."""


@dataclass
class KeycloakGroup:
    id: str
    name: str
    path: str | None = None


@dataclass
class KeycloakUser:
    id: str
    username: str
    email: str | None
    enabled: bool
    first_name: str | None = None
    last_name: str | None = None


class KeycloakAdminClient:
    """Thin async wrapper around Keycloak Admin REST APIs."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.keycloak_admin_url.rstrip("/")
        self.realm = settings.keycloak_realm
        self.admin_user = settings.keycloak_admin_user
        self.admin_password = settings.keycloak_admin_password
        self.admin_client_id = settings.keycloak_admin_client_id

    async def _admin_token(self) -> str:
        token_url = f"{self.base_url}/realms/master/protocol/openid-connect/token"
        payload = {
            "grant_type": "password",
            "client_id": self.admin_client_id,
            "username": self.admin_user,
            "password": self.admin_password,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(token_url, data=payload)
        if response.status_code != 200:
            raise KeycloakAdminError(f"token_error status={response.status_code} body={response.text}")

        token = response.json().get("access_token")
        if not token:
            raise KeycloakAdminError("token_error missing_access_token")
        return str(token)

    async def _request(self, method: str, path: str, *, params=None, json_body=None, expected_status: tuple[int, ...] = (200,)):
        token = await self._admin_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/admin/realms/{self.realm}{path}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(method, url, headers=headers, params=params, json=json_body)
        if response.status_code not in expected_status:
            raise KeycloakAdminError(
                f"keycloak_admin_error method={method} path={path} status={response.status_code} body={response.text}"
            )
        return response

    async def list_groups(self) -> list[KeycloakGroup]:
        response = await self._request("GET", "/groups", params={"briefRepresentation": "true"}, expected_status=(200,))
        groups = response.json() if isinstance(response.json(), list) else []
        output: list[KeycloakGroup] = []
        for row in groups:
            gid = str(row.get("id") or "")
            name = str(row.get("name") or "")
            if not gid or not name:
                continue
            output.append(KeycloakGroup(id=gid, name=name, path=row.get("path")))
        return output

    async def list_users(self, *, search: str | None = None, max_users: int = 100) -> list[KeycloakUser]:
        params = {"max": max(1, min(int(max_users), 500))}
        if search:
            params["search"] = search
        response = await self._request("GET", "/users", params=params, expected_status=(200,))
        users = response.json() if isinstance(response.json(), list) else []
        output: list[KeycloakUser] = []
        for row in users:
            user_id = str(row.get("id") or "")
            username = str(row.get("username") or "")
            if not user_id or not username:
                continue
            output.append(
                KeycloakUser(
                    id=user_id,
                    username=username,
                    email=row.get("email"),
                    enabled=bool(row.get("enabled", True)),
                    first_name=row.get("firstName"),
                    last_name=row.get("lastName"),
                )
            )
        return output

    async def _group_index(self) -> dict[str, KeycloakGroup]:
        groups = await self.list_groups()
        return {group.name.strip().lower(): group for group in groups}

    async def _find_user_id(self, username: str) -> str:
        users = await self.list_users(search=username, max_users=20)
        for user in users:
            if user.username == username:
                return user.id
        raise KeycloakAdminError(f"user_not_found username={username}")

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        groups: list[str],
        first_name: str | None,
        last_name: str | None,
        enabled: bool,
    ) -> tuple[str, list[str]]:
        payload = {
            "username": username,
            "email": email,
            "enabled": enabled,
            "emailVerified": True,
            "firstName": first_name,
            "lastName": last_name,
        }
        await self._request("POST", "/users", json_body=payload, expected_status=(201, 409))

        user_id = await self._find_user_id(username)

        await self._request(
            "PUT",
            f"/users/{user_id}/reset-password",
            json_body={"type": "password", "value": password, "temporary": False},
            expected_status=(204,),
        )

        final_groups = await self.set_user_groups(user_id=user_id, groups=groups)
        return user_id, final_groups

    async def set_user_groups(self, *, user_id: str, groups: list[str]) -> list[str]:
        normalized_target = sorted({item.strip() for item in groups if item and item.strip()})

        all_groups = await self._group_index()
        missing = [name for name in normalized_target if name.lower() not in all_groups]
        if missing:
            raise KeycloakAdminError(f"unknown_groups: {', '.join(missing)}")

        current_response = await self._request("GET", f"/users/{user_id}/groups", expected_status=(200,))
        current_groups = current_response.json() if isinstance(current_response.json(), list) else []
        current_index = {
            str(item.get("name") or "").strip().lower(): str(item.get("id") or "")
            for item in current_groups
            if item.get("name") and item.get("id")
        }

        target_index = {name.lower(): all_groups[name.lower()] for name in normalized_target}

        for current_name, current_id in current_index.items():
            if current_name not in target_index and current_id:
                await self._request("DELETE", f"/users/{user_id}/groups/{current_id}", expected_status=(204,))

        for target_name, target_group in target_index.items():
            if target_name not in current_index:
                await self._request("PUT", f"/users/{user_id}/groups/{target_group.id}", expected_status=(204,))

        return normalized_target
