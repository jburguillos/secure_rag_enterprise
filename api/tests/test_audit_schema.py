from __future__ import annotations

from pathlib import Path


def _schema_path() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / ".." / "infra" / "postgres" / "init" / "001_schema.sql",
        here.parents[1] / "infra" / "postgres" / "init" / "001_schema.sql",
        Path("/app") / "infra" / "postgres" / "init" / "001_schema.sql",
        Path("/infra") / "postgres" / "init" / "001_schema.sql",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    raise FileNotFoundError("Could not locate infra/postgres/init/001_schema.sql")


def test_append_only_triggers_declared() -> None:
    text = _schema_path().read_text(encoding="utf-8")
    assert "prevent_table_mutation" in text
    assert "BEFORE UPDATE ON query_runs" in text
    assert "BEFORE DELETE ON query_run_evidence" in text
