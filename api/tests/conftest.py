from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPA_FAIL_CLOSED", "true")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    from app.config import get_settings, get_yaml_config

    get_settings.cache_clear()
    get_yaml_config.cache_clear()
    yield
    get_settings.cache_clear()
    get_yaml_config.cache_clear()
