"""OPA policy client."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx

from app.auth.context import Entitlements
from app.config import get_settings


@dataclass
class PolicyResult:
    decision_id: UUID
    allow: bool
    reason: str
    policy_version: str = "1.0"
    raw_result: dict | None = None


class PolicyClient:
    """Evaluate access decisions through OPA."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def evaluate(self, *, entitlements: Entitlements, resource_acl: dict, transitional_drive_acl: bool = True) -> PolicyResult:
        decision_id = uuid4()
        input_payload = {
            "user": {
                "authenticated": entitlements.authenticated,
                "user_id": entitlements.user_id or "",
                "email": entitlements.email or "",
                "domain": entitlements.domain or "",
                "groups": [g.lower() for g in entitlements.groups],
            },
            "resource": {
                "allowed_users": [u.lower() for u in (resource_acl.get("allowed_users") or [])],
                "allowed_groups": [g.lower() for g in (resource_acl.get("allowed_groups") or [])],
                "allowed_emails": [u.lower() for u in (resource_acl.get("allowed_emails") or [])],
                "allowed_domains": [d.lower() for d in (resource_acl.get("allowed_domains") or [])],
                "is_public": bool(resource_acl.get("is_public", False)),
            },
            "transitional_drive_acl": transitional_drive_acl,
        }

        opa_url = self.settings.opa_url.rstrip("/") + self.settings.opa_policy_path
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.post(opa_url, json={"input": input_payload})
                response.raise_for_status()
                data = response.json()
                result = data.get("result") or {}
        except Exception:
            if self.settings.opa_fail_closed:
                return PolicyResult(
                    decision_id=decision_id,
                    allow=False,
                    reason="opa_unavailable_fail_closed",
                    policy_version="1.0",
                    raw_result={"error": "opa_unavailable"},
                )
            return PolicyResult(
                decision_id=decision_id,
                allow=True,
                reason="opa_unavailable_fail_open",
                policy_version="1.0",
                raw_result={"error": "opa_unavailable"},
            )

        return PolicyResult(
            decision_id=decision_id,
            allow=bool(result.get("allow", False)),
            reason=str(result.get("reason", "default_deny")),
            policy_version=str(result.get("policy_version", "1.0")),
            raw_result=result,
        )
