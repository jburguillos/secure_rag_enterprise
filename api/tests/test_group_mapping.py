from __future__ import annotations

from app.auth.context import Entitlements
from app.auth.group_mapping import apply_drive_group_mapping


def test_apply_drive_group_mapping_with_json_map(monkeypatch) -> None:
    monkeypatch.setenv("DRIVE_GROUP_MAP_JSON", '{"hr":["hr-shared@enterprise.com", "peopleops@enterprise.com"]}')

    ent = Entitlements(authenticated=True, email="hr.user@example.com", groups=["HR"])
    mapped = apply_drive_group_mapping(ent)

    assert "hr" in mapped.groups
    assert "hr-shared@enterprise.com" in mapped.groups
    assert "peopleops@enterprise.com" in mapped.groups


def test_apply_drive_group_mapping_ignores_invalid_json(monkeypatch) -> None:
    monkeypatch.setenv("DRIVE_GROUP_MAP_JSON", "not-json")

    ent = Entitlements(authenticated=True, email="hr.user@example.com", groups=["HR"])
    mapped = apply_drive_group_mapping(ent)

    assert mapped.groups == ["HR"]

