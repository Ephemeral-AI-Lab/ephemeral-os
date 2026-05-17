"""Tests for the one-shot legacy tier-table drop script."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "drop_legacy_tier_tables.py"
)
_SPEC = importlib.util.spec_from_file_location("drop_legacy_tier_tables", _SCRIPT_PATH)
assert _SPEC is not None
_SCRIPT = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_SCRIPT)


def test_drop_statement_uses_cascade_only_when_requested() -> None:
    assert _SCRIPT._drop_table_statement("episodes", cascade=True) == (
        'DROP TABLE IF EXISTS "episodes" CASCADE'
    )
    assert _SCRIPT._drop_table_statement("episodes", cascade=False) == (
        'DROP TABLE IF EXISTS "episodes"'
    )


def test_drop_legacy_tier_tables_remains_idempotent_for_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE missions (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE episodes (id TEXT PRIMARY KEY)"))

    assert _SCRIPT.drop_legacy_tier_tables(db_url) == ["episodes", "missions"]
    assert _SCRIPT.drop_legacy_tier_tables(db_url) == []

    assert "episodes" not in inspect(engine).get_table_names()
    assert "missions" not in inspect(engine).get_table_names()
