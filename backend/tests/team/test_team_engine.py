from __future__ import annotations

from types import SimpleNamespace

from team.persistence import team_engine


class _FakeConn:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


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


def test_normalize_legacy_ltree_columns_rewrites_legacy_types(monkeypatch):
    engine = _FakeEngine()
    legacy_types = {
        ("tasks", "scope_ltree"): "ltree[]",
    }
    monkeypatch.setattr(
        team_engine,
        "_legacy_column_type",
        lambda _engine, table_name, column_name: legacy_types.get((table_name, column_name)),
    )

    team_engine._normalize_legacy_ltree_columns(engine)

    assert engine.conn.statements == [
        "ALTER TABLE tasks ALTER COLUMN scope_ltree TYPE TEXT[] USING COALESCE(scope_ltree::text[], ARRAY[]::text[])",
    ]


def test_normalize_legacy_ltree_columns_skips_non_postgres(monkeypatch):
    engine = _FakeEngine(dialect_name="sqlite")
    called = False

    def _unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(team_engine, "_legacy_column_type", _unexpected)

    team_engine._normalize_legacy_ltree_columns(engine)

    assert called is False
    assert engine.conn.statements == []
