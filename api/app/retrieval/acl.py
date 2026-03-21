"""ACL helpers and retrieval-time filters for authorization enforcement."""

from __future__ import annotations

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from app.auth.context import Entitlements


def _nested_acl(payload: dict, *path: str):
    current = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def extract_acl_payload(payload: dict) -> dict:
    """Return a normalized ACL view from flat or nested payload shapes.

    Older or environment-specific ingests may keep ACL fields nested under
    ``permissions_summary``. Retrieval and defense-in-depth checks should treat
    both shapes equivalently.
    """

    permissions_summary = _nested_acl(payload, "permissions_summary")
    metadata_permissions = _nested_acl(payload, "metadata", "permissions_summary")

    def _pick_list(key: str) -> list:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        for candidate in (permissions_summary, metadata_permissions):
            if isinstance(candidate, dict):
                nested = candidate.get(key)
                if isinstance(nested, list):
                    return nested
        return []

    def _pick_bool(key: str) -> bool:
        value = payload.get(key)
        if value is not None:
            return bool(value)
        for candidate in (permissions_summary, metadata_permissions):
            if isinstance(candidate, dict) and key in candidate:
                return bool(candidate.get(key))
        return False

    return {
        "allowed_emails": _pick_list("allowed_emails"),
        "allowed_domains": _pick_list("allowed_domains"),
        "allowed_users": _pick_list("allowed_users"),
        "allowed_groups": _pick_list("allowed_groups"),
        "is_public": _pick_bool("is_public"),
    }


def build_acl_filter(entitlements: Entitlements) -> Filter:
    """Build strict allow-list filter for Qdrant payload filtering."""

    acl_keys = {
        "is_public": ("is_public", "permissions_summary.is_public", "metadata.permissions_summary.is_public"),
        "allowed_emails": (
            "allowed_emails",
            "permissions_summary.allowed_emails",
            "metadata.permissions_summary.allowed_emails",
        ),
        "allowed_domains": (
            "allowed_domains",
            "permissions_summary.allowed_domains",
            "metadata.permissions_summary.allowed_domains",
        ),
        "allowed_users": (
            "allowed_users",
            "permissions_summary.allowed_users",
            "metadata.permissions_summary.allowed_users",
        ),
        "allowed_groups": (
            "allowed_groups",
            "permissions_summary.allowed_groups",
            "metadata.permissions_summary.allowed_groups",
        ),
    }

    should: list[FieldCondition] = [
        FieldCondition(key=key, match=MatchValue(value=True)) for key in acl_keys["is_public"]
    ]

    if entitlements.email:
        email = entitlements.email.lower()
        for key in acl_keys["allowed_emails"]:
            should.append(FieldCondition(key=key, match=MatchValue(value=email)))
            should.append(FieldCondition(key=key, match=MatchAny(any=[email])))
    if entitlements.domain:
        domain = entitlements.domain.lower()
        for key in acl_keys["allowed_domains"]:
            should.append(FieldCondition(key=key, match=MatchValue(value=domain)))
            should.append(FieldCondition(key=key, match=MatchAny(any=[domain])))
    if entitlements.user_id:
        user_id = entitlements.user_id.lower()
        for key in acl_keys["allowed_users"]:
            should.append(FieldCondition(key=key, match=MatchValue(value=user_id)))
            should.append(FieldCondition(key=key, match=MatchAny(any=[user_id])))

    for group in sorted({g.lower() for g in entitlements.groups}):
        for key in acl_keys["allowed_groups"]:
            should.append(FieldCondition(key=key, match=MatchValue(value=group)))
            should.append(FieldCondition(key=key, match=MatchAny(any=[group])))

    return Filter(should=should)


def payload_access_allowed(payload: dict, entitlements: Entitlements) -> bool:
    """Reference ACL evaluator used by tests and defense-in-depth checks."""

    acl = extract_acl_payload(payload)

    if bool(acl.get("is_public", False)):
        return True

    allowed_emails = {str(v).lower() for v in (acl.get("allowed_emails") or [])}
    allowed_domains = {str(v).lower() for v in (acl.get("allowed_domains") or [])}
    allowed_users = {str(v).lower() for v in (acl.get("allowed_users") or [])}
    allowed_groups = {str(v).lower() for v in (acl.get("allowed_groups") or [])}

    if entitlements.email and entitlements.email.lower() in allowed_emails:
        return True
    if entitlements.domain and entitlements.domain.lower() in allowed_domains:
        return True
    if entitlements.user_id and entitlements.user_id.lower() in allowed_users:
        return True
    if any(group.lower() in allowed_groups for group in entitlements.groups):
        return True

    return False
