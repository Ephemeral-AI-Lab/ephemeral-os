"""Migration test: legacy task_center_harness_graph table is dropped."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

import db.engine as engine_mod
from config.settings import DatabaseSettings


def test_initialize_db_drops_legacy_harness_graph_table(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"

    # Pre-seed the legacy table with a row to confirm it gets dropped.
    pre_engine = create_engine(f"sqlite:///{db_path}")
    with pre_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE task_center_harness_graph "
                "(id TEXT PRIMARY KEY, run_id TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO task_center_harness_graph (id, run_id) "
                "VALUES ('legacy-1', 'r1')"
            )
        )
    pre_engine.dispose()

    # Reset module-level engine state so initialize_db rebuilds cleanly.
    monkeypatch.setattr(engine_mod, "_engine", None)
    monkeypatch.setattr(engine_mod, "_session_factory", None)
    monkeypatch.setattr(engine_mod, "_async_engine", None)
    monkeypatch.setattr(engine_mod, "_async_session_factory", None)

    sf = engine_mod.initialize_db(DatabaseSettings(url=f"sqlite:///{db_path}"))
    assert sf is not None
    eng = engine_mod.get_engine()
    assert eng is not None
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "task_center_harness_graph" not in tables
    assert "complex_task_requests" in tables
    assert "task_segments" in tables
    assert "harness_graphs" in tables
