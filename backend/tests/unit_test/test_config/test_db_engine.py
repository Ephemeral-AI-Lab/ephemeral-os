"""Database engine bootstrap and lightweight schema migration tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import db.engine as engine_mod
import db.models  # noqa: F401 - populate Base.metadata
from config.settings import DatabaseSettings
from db.stores.agent_run_store import AgentRunStore
from db.stores.task_store import TaskStore


@pytest.fixture(autouse=True)
def reset_db_engine_state() -> Iterator[None]:
    _reset_db_engine_state()
    try:
        yield
    finally:
        _reset_db_engine_state()


def _reset_db_engine_state() -> None:
    if engine_mod._engine is not None:
        engine_mod._engine.dispose()
    engine_mod._engine = None
    engine_mod._session_factory = None


def test_initialize_db_migrates_legacy_agent_runs_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "legacy.db"
    legacy_engine = create_engine(f"sqlite:///{db_path}")
    with legacy_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sessions (
                    id VARCHAR(36) NOT NULL,
                    PRIMARY KEY (id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE agent_runs (
                    id VARCHAR(36) NOT NULL,
                    session_id VARCHAR(36) NOT NULL,
                    agent_name VARCHAR(128) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    input_query TEXT,
                    response JSON,
                    message_history JSON,
                    compacted_history JSON,
                    reasoning TEXT,
                    error TEXT,
                    event_count INTEGER NOT NULL,
                    metadata JSON,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME NOT NULL,
                    PRIMARY KEY (id),
                    FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX ix_agent_runs_session_id ON agent_runs (session_id)"))
    legacy_engine.dispose()

    sf = engine_mod.initialize_db(DatabaseSettings(url=f"sqlite:///{db_path}"))
    assert sf is not None

    engine = engine_mod.get_engine()
    assert engine is not None
    insp = inspect(engine)
    agent_columns = {col["name"] for col in insp.get_columns("agent_runs")}
    assert "session_id" not in agent_columns
    assert "status" not in agent_columns
    assert {"task_id", "terminal_tool_result", "token_count"} <= agent_columns
    assert not any(index["name"] == "ix_agent_runs_session_id" for index in insp.get_indexes("agent_runs"))

    task_store = TaskStore()
    task_store.initialize(sf)
    agent_run_store = AgentRunStore()
    agent_run_store.initialize(sf)

    task_store.create_request(
        request_id="req",
        cwd="/repo",
        sandbox_id=None,
        request_prompt="prompt",
    )
    task_store.upsert_task(
        task_id="task1",
        request_id="req",
        role="executor",
        agent_name="executor",
        instruction="prompt",
        status="running",
        outcomes=[],
        needs=[],
    )

    agent_run_store.create_run(
        agent_run_id="agent1",
        task_id="task1",
        agent_name="executor",
    )

    tasks = task_store.list_tasks_for_request("req")
    assert tasks[0]["request_id"] == "req"
    assert "task_center_run_id" not in tasks[0]
    assert agent_run_store.get_run("agent1") is not None


def test_initialize_db_fresh_sqlite_creates_instruction_column(
    tmp_path: Path,
) -> None:
    """Fresh DB boot creates ``instruction`` for first-class tasks."""
    db_path = tmp_path / "fresh.db"
    sf = engine_mod.initialize_db(DatabaseSettings(url=f"sqlite:///{db_path}"))
    assert sf is not None
    engine = engine_mod.get_engine()
    assert engine is not None
    columns = {col["name"] for col in inspect(engine).get_columns("tasks")}
    assert "instruction" in columns
    assert "context_message" not in columns


def test_initialize_db_drops_legacy_task_center_tables(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dead-columns.db"
    legacy_engine = create_engine(f"sqlite:///{db_path}")
    with legacy_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE task_center_tasks (
                    id VARCHAR(96) NOT NULL,
                    task_center_run_id VARCHAR(36),
                    context_message TEXT,
                    system_prompt TEXT,
                    user_prompt TEXT,
                    PRIMARY KEY (id)
                )
                """
            )
        )
    legacy_engine.dispose()

    sf = engine_mod.initialize_db(DatabaseSettings(url=f"sqlite:///{db_path}"))
    assert sf is not None

    engine = engine_mod.get_engine()
    assert engine is not None
    insp = inspect(engine)
    assert not insp.has_table("task_center_tasks")
