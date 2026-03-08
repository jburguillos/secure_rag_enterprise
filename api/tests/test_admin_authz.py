from __future__ import annotations

from app.admin.authz import has_admin_role, parse_admin_groups
from app.auth.context import Entitlements


def test_parse_admin_groups_normalizes() -> None:
    groups = parse_admin_groups("admin, /SecOps, FINANCE")
    assert groups == {"admin", "secops", "finance"}


def test_has_admin_role_true() -> None:
    ent = Entitlements(authenticated=True, groups=["HR", "/Admin"])
    assert has_admin_role(ent, {"admin"})


def test_has_admin_role_false() -> None:
    ent = Entitlements(authenticated=True, groups=["HR"])
    assert not has_admin_role(ent, {"admin"})
