from __future__ import annotations

from app.config import get_settings


def test_prod_safety_defaults() -> None:
    settings = get_settings()
    assert settings.allow_outbound is False
    assert settings.allow_public_llm is False
