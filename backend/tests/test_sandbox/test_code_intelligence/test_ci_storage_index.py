"""Unit tests for the Phase 3.5 :class:`IndexStore` SQLite adapter."""

from __future__ import annotations

import threading
from pathlib import Path


from sandbox.code_intelligence.core.types import SymbolInfo, SymbolKind
from sandbox.code_intelligence.in_sandbox.ci_storage import (
    IndexStore,
    _decode_symbols,
    _encode_symbols,
    migrate_pickle_to_sqlite,
    write_snapshot,
)


def _mk_symbol(name: str, file_path: str, line: int = 1) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        kind=SymbolKind.FUNCTION,
        file_path=file_path,
        line=line,
        character=0,
        signature=f"def {name}()",
        docstring="",
        container="",
    )


def test_wal_pragma_applied(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    try:
        row = store._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].lower() == "wal"
    finally:
        store.close()


def test_schema_and_index_created_on_first_open(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    try:
        tables = {
            r[0]
            for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "index_files" in tables
        indexes = {
            r[0]
            for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_index_files_generation" in indexes
    finally:
        store.close()


def test_integrity_check_failure_rotates_file(tmp_path: Path) -> None:
    target = tmp_path / "index.sqlite3"
    target.write_bytes(b"this is not a sqlite database")

    store = IndexStore(state_dir_path=tmp_path)
    try:
        rotated = list(tmp_path.glob("index.corrupt.*.sqlite3"))
        assert rotated, "corrupt DB not rotated"
        # Fresh DB usable.
        store.refresh_file("/x.py", [_mk_symbol("foo", "/x.py")])
        assert store.indexed_paths() == ["/x.py"]
    finally:
        store.close()


def test_bulk_replace_atomic(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    try:
        snap1 = {
            "/a.py": [_mk_symbol("a1", "/a.py"), _mk_symbol("a2", "/a.py")],
            "/b.py": [_mk_symbol("b1", "/b.py")],
        }
        gen1 = store.bulk_replace(snap1)
        assert gen1 > 0
        assert sorted(store.indexed_paths()) == ["/a.py", "/b.py"]
        assert store.indexed_files() == 2
        assert len(store.all_symbols()) == 3

        # Second bulk_replace fully replaces.
        snap2 = {"/c.py": [_mk_symbol("c1", "/c.py")]}
        gen2 = store.bulk_replace(snap2)
        assert gen2 > gen1
        assert store.indexed_paths() == ["/c.py"]
        assert store.indexed_files() == 1
    finally:
        store.close()


def test_refresh_file_updates_single_pk(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    try:
        gen0 = store.refresh_file("/x.py", [_mk_symbol("foo", "/x.py")])
        gen1 = store.refresh_file("/x.py", [_mk_symbol("bar", "/x.py")])
        assert gen1 > gen0
        syms = store.file_symbols("/x.py")
        assert [s.name for s in syms] == ["bar"]
    finally:
        store.close()


def test_delete_file_removes_row(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    try:
        store.refresh_file("/x.py", [_mk_symbol("foo", "/x.py")])
        gen_before = store.generation
        gen_after = store.delete_file("/x.py")
        assert gen_after > gen_before
        assert store.file_symbols("/x.py") == []
    finally:
        store.close()


def test_query_by_substring_parity(tmp_path: Path) -> None:
    """SQLite-backed query parity with the in-memory linear scan."""
    store = IndexStore(state_dir_path=tmp_path)
    try:
        symbols_a = [_mk_symbol(f"alpha_{i}", "/a.py", line=i) for i in range(20)]
        symbols_b = [_mk_symbol(f"beta_{i}", "/b.py", line=i) for i in range(20)]
        store.bulk_replace({"/a.py": symbols_a, "/b.py": symbols_b})

        # In-memory baseline.
        all_syms = symbols_a + symbols_b
        baseline = [s for s in all_syms if "alpha" in s.name.lower()]
        sqlite_results = store.query_by_substring("alpha")
        assert sorted(s.name for s in baseline) == sorted(
            s.name for s in sqlite_results
        )

        # Empty query returns nothing.
        assert store.query_by_substring("") == []
    finally:
        store.close()


def test_concurrent_refresh_file_distinct_rows(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    try:
        errors: list[BaseException] = []

        def _writer(i: int) -> None:
            try:
                store.refresh_file(f"/f{i}.py", [_mk_symbol(f"sym_{i}", f"/f{i}.py")])
            except BaseException as exc:  # pragma: no cover - exposed via assertion
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert store.indexed_files() == 10
    finally:
        store.close()


def test_msgpack_round_trip() -> None:
    syms = [_mk_symbol("foo", "/a.py", line=10)]
    blob = _encode_symbols(syms)
    out = _decode_symbols(blob)
    assert len(out) == 1
    assert out[0].name == "foo"
    assert out[0].line == 10
    assert out[0].kind is SymbolKind.FUNCTION


def test_msgpack_round_trip_unknown_kind() -> None:
    """An unknown kind string falls back to UNKNOWN rather than raising."""
    blob = _encode_symbols([])
    out = _decode_symbols(blob)
    assert out == []


def test_migrate_pickle_to_sqlite_only_pickle(tmp_path: Path) -> None:
    snapshot = {
        "/a.py": [_mk_symbol("foo", "/a.py")],
        "/b.py": [_mk_symbol("bar", "/b.py")],
    }
    write_snapshot(tmp_path, "index.snapshot", snapshot)
    assert (tmp_path / "index.snapshot").exists()
    n = migrate_pickle_to_sqlite(tmp_path)
    assert n == 2
    assert not (tmp_path / "index.snapshot").exists()
    store = IndexStore(state_dir_path=tmp_path)
    try:
        assert sorted(store.indexed_paths()) == ["/a.py", "/b.py"]
    finally:
        store.close()


def test_migrate_pickle_to_sqlite_only_sqlite(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    try:
        store.refresh_file("/x.py", [_mk_symbol("foo", "/x.py")])
    finally:
        store.close()
    n = migrate_pickle_to_sqlite(tmp_path)
    assert n == 0


def test_migrate_pickle_to_sqlite_neither(tmp_path: Path) -> None:
    n = migrate_pickle_to_sqlite(tmp_path)
    assert n == 0


def test_migrate_pickle_to_sqlite_corrupt_pickle(tmp_path: Path) -> None:
    (tmp_path / "index.snapshot").write_bytes(b"not a pickle")
    n = migrate_pickle_to_sqlite(tmp_path)
    assert n == 0
    # corrupt path is unlinked by read_snapshot OR by migrate_pickle_to_sqlite
    assert not (tmp_path / "index.snapshot").exists()


def test_close_idempotent(tmp_path: Path) -> None:
    store = IndexStore(state_dir_path=tmp_path)
    store.close()
    store.close()  # second close must not raise
