"""Migration test: legacy task_center_attempt table is dropped."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

import db.engine as engine_mod
from config.settings import DatabaseSettings


def test_initialize_db_drops_legacy_attempt_table(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"

    # Pre-seed the legacy table with a row to confirm it gets dropped.
    pre_engine = create_engine(f"sqlite:///{db_path}")
    with pre_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE task_center_attempt "
                "(id TEXT PRIMARY KEY, run_id TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO task_center_attempt (id, run_id) "
                "VALUES ('legacy-1', 'r1')"
            )
        )
    pre_engine.dispose()

    # Reset module-level engine state so initialize_db rebuilds cleanly.
    monkeypatch.setattr(engine_mod, "_engine", None)
    monkeypatch.setattr(engine_mod, "_session_factory", None)

    sf = engine_mod.initialize_db(DatabaseSettings(url=f"sqlite:///{db_path}"))
    assert sf is not None
    eng = engine_mod.get_engine()
    assert eng is not None
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "task_center_attempt" not in tables
    assert "missions" in tables
    assert "episodes" in tables
    assert "attempts" in tables
