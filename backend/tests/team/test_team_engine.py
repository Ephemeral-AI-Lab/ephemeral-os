from __future__ import annotations

from types import SimpleNamespace

from team.persistence import team_engine


class _FakeEngine:
    dialect = SimpleNamespace(name="sqlite")


def test_ensure_team_schema_registers_models_columns_and_indexes(monkeypatch):
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
        "_add_missing_columns",
        lambda _engine: calls.append("columns"),
    )
    monkeypatch.setattr(
        team_engine,
        "_ensure_indexes",
        lambda _engine: calls.append("indexes"),
    )

    team_engine._ensure_team_schema(engine)

    assert calls == ["models", "create_all", "columns", "indexes"]


def test_create_team_engine_refreshes_sync_engine_after_initialize(monkeypatch):
    initialized = False
    sync_engine = _FakeEngine()
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
