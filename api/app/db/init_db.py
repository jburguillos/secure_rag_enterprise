"""Database bootstrap helpers."""

from __future__ import annotations

from app.db.database import engine
from app.db.models import Base


def init_db() -> None:
    """Create tables for local/dev runs where migrations are not applied."""

    Base.metadata.create_all(bind=engine)
