"""ACL filter builders for retrieval-time enforcement."""

from __future__ import annotations

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from app.auth.context import Entitlements


def build_acl_filter(entitlements: Entitlements) -> Filter:
    """Build strict allow-list filter for Qdrant payload filtering."""

    should: list[FieldCondition] = [FieldCondition(key="is_public", match=MatchValue(value=True))]

    if entitlements.email:
        # ACL fields are stored as string arrays in payload; MatchAny is the
        # most robust operator across Qdrant versions for array membership.
        should.append(FieldCondition(key="allowed_emails", match=MatchAny(any=[entitlements.email.lower()])))
    if entitlements.domain:
        should.append(FieldCondition(key="allowed_domains", match=MatchAny(any=[entitlements.domain.lower()])))
    if entitlements.user_id:
        should.append(FieldCondition(key="allowed_users", match=MatchAny(any=[entitlements.user_id.lower()])))

    for group in sorted({g.lower() for g in entitlements.groups}):
        should.append(FieldCondition(key="allowed_groups", match=MatchAny(any=[group])))

    return Filter(should=should)


def payload_access_allowed(payload: dict, entitlements: Entitlements) -> bool:
    """Reference ACL evaluator used by tests and defense-in-depth checks."""

    if bool(payload.get("is_public", False)):
        return True

    allowed_emails = {str(v).lower() for v in (payload.get("allowed_emails") or [])}
    allowed_domains = {str(v).lower() for v in (payload.get("allowed_domains") or [])}
    allowed_users = {str(v).lower() for v in (payload.get("allowed_users") or [])}
    allowed_groups = {str(v).lower() for v in (payload.get("allowed_groups") or [])}

    if entitlements.email and entitlements.email.lower() in allowed_emails:
        return True
    if entitlements.domain and entitlements.domain.lower() in allowed_domains:
        return True
    if entitlements.user_id and entitlements.user_id.lower() in allowed_users:
        return True
    if any(group.lower() in allowed_groups for group in entitlements.groups):
        return True

    return False
