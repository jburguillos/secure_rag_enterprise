"""Runtime admin settings storage (DB-backed with env fallback)."""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.db.database import get_session
from app.db import repository

DRIVE_GROUP_MAP_KEY = "drive_group_map"


def _normalize_group(value: str) -> str:
    return value.strip().lower().lstrip("/")


def normalize_drive_group_map(raw_mapping: dict[str, Any]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key, raw_values in raw_mapping.items():
        group = _normalize_group(str(key))
        if not group:
            continue

        values: list[str] = []
        if isinstance(raw_values, str):
            values = [_normalize_group(raw_values)]
        elif isinstance(raw_values, list):
            values = [_normalize_group(str(item)) for item in raw_values]

        cleaned = sorted({value for value in values if value})
        if cleaned:
            normalized[group] = cleaned
    return normalized


def parse_drive_group_map_json(raw_json: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return normalize_drive_group_map(payload)


def _read_db_drive_group_map() -> dict[str, list[str]] | None:
    try:
        with get_session() as session:
            value = repository.get_admin_setting(session, DRIVE_GROUP_MAP_KEY)
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    return normalize_drive_group_map(value)


def read_drive_group_map() -> tuple[dict[str, list[str]], str]:
    """Return effective mapping and source marker (`db` or `env`).

    Non-empty env mapping intentionally overrides DB mapping to keep local/test
    behavior deterministic and allow explicit env overrides.
    """

    settings = get_settings()
    env_mapping = parse_drive_group_map_json(settings.drive_group_map_json)
    if env_mapping:
        return env_mapping, "env"

    db_mapping = _read_db_drive_group_map()
    if db_mapping is not None:
        return db_mapping, "db"

    return env_mapping, "env"


def get_effective_drive_group_map() -> dict[str, list[str]]:
    mapping, _source = read_drive_group_map()
    return mapping


def save_drive_group_map(mapping: dict[str, Any]) -> dict[str, list[str]]:
    normalized = normalize_drive_group_map(mapping)
    with get_session() as session:
        repository.upsert_admin_setting(session, DRIVE_GROUP_MAP_KEY, normalized)
    return normalized
