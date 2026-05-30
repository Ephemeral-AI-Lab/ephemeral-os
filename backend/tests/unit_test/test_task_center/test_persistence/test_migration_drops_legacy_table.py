"""Migration test: legacy task_center_attempt table is dropped."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

import db.engine as engine_mod
from config.settings import DatabaseSettings
from db.stores.workflow_store import WorkflowStore


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
    assert "workflows" in tables
    assert "iterations" in tables
    assert "attempts" in tables


def test_initialize_db_creates_workflows_table_on_fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    _reset_engine(monkeypatch)

    sf = engine_mod.initialize_db(DatabaseSettings(url=f"sqlite:///{db_path}"))
    assert sf is not None

    eng = engine_mod.get_engine()
    assert eng is not None
    tables = set(inspect(eng).get_table_names())
    assert "workflows" in tables
    assert "goals" not in tables

    store = WorkflowStore()
    store.initialize(sf)
    workflow = store.insert(task_center_run_id="run1", goal="fresh objective")
    assert store.get(workflow.id) == workflow


def test_initialize_db_renames_task_specification_to_plan_spec(tmp_path, monkeypatch):
    """FU-2: legacy ``task_specification`` columns migrate to ``plan_spec`` on
    both ``iterations`` and ``attempts``, preserving stored values."""
    db_path = tmp_path / "legacy-plan-spec.db"
    pre_engine = create_engine(f"sqlite:///{db_path}")
    with pre_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE iterations (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    creation_reason TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    attempt_budget INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempt_ids JSON,
                    deferred_goal TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    closed_at DATETIME,
                    task_specification TEXT,
                    task_summary TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE attempts (
                    id TEXT PRIMARY KEY,
                    iteration_id TEXT NOT NULL,
                    attempt_sequence_no INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    task_specification TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO iterations (id, workflow_id, sequence_no, "
                "creation_reason, goal, attempt_budget, status, created_at, "
                "updated_at, task_specification) VALUES ('it1', 'wf1', 1, "
                "'initial', 'g', 3, 'succeeded', '2026-01-01 00:00:00', "
                "'2026-01-01 00:00:00', 'iteration spec')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO attempts (id, iteration_id, attempt_sequence_no, "
                "stage, status, created_at, updated_at, task_specification) VALUES "
                "('at1', 'it1', 1, 'closed', 'passed', '2026-01-01 00:00:00', "
                "'2026-01-01 00:00:00', 'attempt spec')"
            )
        )
    pre_engine.dispose()

    _reset_engine(monkeypatch)
    sf = engine_mod.initialize_db(DatabaseSettings(url=f"sqlite:///{db_path}"))
    assert sf is not None
    eng = engine_mod.get_engine()
    assert eng is not None
    insp = inspect(eng)

    for table in ("iterations", "attempts"):
        cols = {column["name"] for column in insp.get_columns(table)}
        assert "plan_spec" in cols, table
        assert "task_specification" not in cols, table

    with eng.begin() as conn:
        assert (
            conn.execute(text("SELECT plan_spec FROM iterations WHERE id='it1'")).scalar_one()
            == "iteration spec"
        )
        assert (
            conn.execute(text("SELECT plan_spec FROM attempts WHERE id='at1'")).scalar_one()
            == "attempt spec"
        )


def _reset_engine(monkeypatch) -> None:
    monkeypatch.setattr(engine_mod, "_engine", None)
    monkeypatch.setattr(engine_mod, "_session_factory", None)
