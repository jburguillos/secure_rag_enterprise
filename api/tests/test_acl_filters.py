from __future__ import annotations

from app.auth.context import Entitlements
from app.retrieval.acl import build_acl_filter, payload_access_allowed


def test_build_acl_filter_includes_public_and_email_domain() -> None:
    ent = Entitlements(authenticated=True, email="hr.user@example.com", domain="example.com", groups=["hr"])
    qfilter = build_acl_filter(ent)
    keys = [item.key for item in (qfilter.should or [])]
    assert "is_public" in keys
    assert "allowed_emails" in keys
    assert "allowed_domains" in keys
    assert "allowed_groups" in keys


def test_public_doc_allowed() -> None:
    ent = Entitlements(authenticated=False)
    payload = {"is_public": True}
    assert payload_access_allowed(payload, ent)


def test_hr_cannot_access_finance_only_doc() -> None:
    ent = Entitlements(authenticated=True, email="hr.user@example.com", domain="example.com", groups=["hr"])
    payload = {"is_public": False, "allowed_groups": ["finance"]}
    assert not payload_access_allowed(payload, ent)


def test_finance_cannot_access_hr_only_doc() -> None:
    ent = Entitlements(authenticated=True, email="finance.user@example.com", domain="example.com", groups=["finance"])
    payload = {"is_public": False, "allowed_groups": ["hr"]}
    assert not payload_access_allowed(payload, ent)


def test_drive_transitional_email_domain_acl() -> None:
    ent_email = Entitlements(authenticated=True, email="alice@example.com", domain="example.com")
    ent_domain = Entitlements(authenticated=True, email="bob@corp.com", domain="corp.com")

    email_doc = {"is_public": False, "allowed_emails": ["alice@example.com"]}
    domain_doc = {"is_public": False, "allowed_domains": ["corp.com"]}

    assert payload_access_allowed(email_doc, ent_email)
    assert payload_access_allowed(domain_doc, ent_domain)
