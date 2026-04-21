from __future__ import annotations

from types import SimpleNamespace

import pytest

from team.persistence import team_engine


class _FakeConn:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, statement) -> None:
        self.statements.append(statement)


class _FakeBegin:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def __enter__(self) -> _FakeConn:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeEngine:
    def __init__(self, dialect_name: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.conn = _FakeConn()

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.conn)


def test_reject_unsupported_legacy_ltree_columns(monkeypatch):
    engine = _FakeEngine()
    legacy_types = {
        ("tasks", "scope_ltree"): "ltree[]",
    }
    monkeypatch.setattr(
        team_engine,
        "_legacy_column_type",
        lambda _engine, table_name, column_name: legacy_types.get((table_name, column_name)),
    )

    with pytest.raises(RuntimeError, match="Unsupported legacy schema detected at tasks.scope_ltree"):
        team_engine._reject_unsupported_legacy_columns(engine)

    assert engine.conn.statements == []


def test_reject_unsupported_legacy_columns_skips_non_postgres(monkeypatch):
    engine = _FakeEngine(dialect_name="sqlite")
    called = False

    def _unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(team_engine, "_legacy_column_type", _unexpected)

    team_engine._reject_unsupported_legacy_columns(engine)

    assert called is False
    assert engine.conn.statements == []


def test_reject_unsupported_legacy_task_columns(monkeypatch):
    engine = _FakeEngine()
    legacy_types = {
        ("tasks", "task"): "text",
    }
    monkeypatch.setattr(
        team_engine,
        "_legacy_column_type",
        lambda _engine, table_name, column_name: legacy_types.get((table_name, column_name)),
    )

    with pytest.raises(RuntimeError, match="Unsupported legacy schema detected at tasks.task"):
        team_engine._reject_unsupported_legacy_columns(engine)

    assert engine.conn.statements == []


def test_reject_unsupported_legacy_columns_skips_missing_column(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(
        team_engine,
        "_legacy_column_type",
        lambda *_args, **_kwargs: None,
    )

    team_engine._reject_unsupported_legacy_columns(engine)

    assert engine.conn.statements == []


def test_ensure_supported_column_types_removes_legacy_status_length(monkeypatch):
    engine = _FakeEngine()
    legacy_types = {
        ("tasks", "status"): "character varying(16)",
    }
    monkeypatch.setattr(
        team_engine,
        "_legacy_column_type",
        lambda _engine, table_name, column_name: legacy_types.get((table_name, column_name)),
    )

    team_engine._ensure_supported_column_types(engine)

    assert len(engine.conn.statements) == 1
    assert (
        str(engine.conn.statements[0])
        == 'ALTER TABLE "tasks" ALTER COLUMN "status" TYPE TEXT'
    )


def test_ensure_supported_column_types_removes_wide_status_length(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(
        team_engine,
        "_legacy_column_type",
        lambda _engine, table_name, column_name: "character varying(32)",
    )

    team_engine._ensure_supported_column_types(engine)

    assert len(engine.conn.statements) == 1
    assert (
        str(engine.conn.statements[0])
        == 'ALTER TABLE "tasks" ALTER COLUMN "status" TYPE TEXT'
    )


def test_ensure_supported_column_types_leaves_text_status(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(
        team_engine,
        "_legacy_column_type",
        lambda _engine, table_name, column_name: "text",
    )

    team_engine._ensure_supported_column_types(engine)

    assert engine.conn.statements == []


def test_ensure_team_schema_repairs_supported_column_type_drift(monkeypatch):
    engine = _FakeEngine()
    calls: list[str] = []

    monkeypatch.setattr(
        team_engine,
        "_ensure_team_models_registered",
        lambda: calls.append("models"),
    )
    monkeypatch.setattr(
        team_engine.Base.metadata,
        "create_all",
        lambda _engine: calls.append("create_all"),
    )
    monkeypatch.setattr(
        team_engine,
        "_reject_unsupported_legacy_columns",
        lambda _engine: calls.append("reject"),
    )
    monkeypatch.setattr(
        team_engine,
        "_ensure_supported_column_types",
        lambda _engine: calls.append("types"),
    )
    monkeypatch.setattr(
        team_engine,
        "_add_missing_columns",
        lambda _engine: calls.append("columns"),
    )
    monkeypatch.setattr(
        team_engine,
        "_ensure_indexes",
        lambda _engine: calls.append("indexes"),
    )

    team_engine._ensure_team_schema(engine)

    assert calls == ["models", "create_all", "reject", "types", "columns", "indexes"]


def test_create_team_engine_refreshes_sync_engine_after_initialize(monkeypatch):
    initialized = False
    sync_engine = _FakeEngine(dialect_name="sqlite")
    async_engine = object()
    async_factory = object()
    ensured: list[object] = []

    def _initialize(_settings):
        nonlocal initialized
        initialized = True

    monkeypatch.setattr(team_engine, "get_session_factory", lambda: None)
    monkeypatch.setattr(team_engine, "initialize_db", _initialize)
    monkeypatch.setattr(
        team_engine,
        "get_engine",
        lambda: sync_engine if initialized else None,
    )
    monkeypatch.setattr(team_engine, "get_async_engine", lambda: async_engine)
    monkeypatch.setattr(team_engine, "get_async_session_factory", lambda: async_factory)
    monkeypatch.setattr(team_engine, "_ensure_team_schema", lambda engine: ensured.append(engine))

    result_engine, result_factory = team_engine.create_team_engine(
        SimpleNamespace(database=object())
    )

    assert result_engine is async_engine
    assert result_factory is async_factory
    assert ensured == [sync_engine]
