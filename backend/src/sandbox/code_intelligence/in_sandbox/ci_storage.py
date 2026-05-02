"""Storage layer for the in-sandbox CI runtime.

Owns the ``$HOME/.cache/eos-ci/<workspace_root_hash>/v1/`` resolver, an atomic
snapshot writer, an integrity-checking reader, and a load-bearing
path-confinement guard.

Phase 1 ships pickle snapshots; Phase 3.5 will swap in SQLite without
changing the public ``write_snapshot`` / ``read_snapshot`` API.
Phase 3 adds :class:`LedgerStore` — a SQLite-WAL backed adapter that
implements the same duck-typed interface as
``mutations.edit_history_ledger.EditHistoryLedger``.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import pickle
import sqlite3
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "CiStoragePathEscape",
    "CiStorageUnavailable",
    "IndexStore",
    "LedgerStore",
    "_confine",
    "migrate_pickle_to_sqlite",
    "read_snapshot",
    "state_dir",
    "workspace_root_hash",
    "write_snapshot",
]

logger = logging.getLogger(__name__)


class CiStorageUnavailable(Exception):
    """Raised when ``$HOME/.cache/eos-ci/...`` cannot be created or written.

    Carries the ``errno`` and resolved path so the Phase 1 privilege probe
    can fail loud with the exact context.
    """

    def __init__(self, errno: int, path: str, message: str) -> None:
        super().__init__(message)
        self.errno = errno
        self.path = path
        self.message = message


class CiStoragePathEscape(Exception):
    """Raised when a write target escapes the state-dir confinement."""


def workspace_root_hash(workspace_root: str) -> str:
    """Stable 16-hex digest of ``realpath(workspace_root)``.

    Symlinks resolve to the same hash as their target — ``ci_index`` and the
    daemon must agree on the snapshot location even when callers pass
    differently-symlinked paths.
    """
    real = os.path.realpath(workspace_root)
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


def state_dir(workspace_root: str) -> Path:
    """Resolve ``$HOME/.cache/eos-ci/<wh>/v1/`` and ``mkdir -p``.

    Raises :class:`CiStorageUnavailable` if the directory cannot be created
    (typically a privilege failure on a sandbox image where ``$HOME`` is not
    writable). Does NOT silently fall back to ``/tmp`` — surfacing the
    failure is load-bearing for the Phase 1 privilege probe.
    """
    home = Path(os.path.expanduser("~"))
    base = home / ".cache" / "eos-ci" / workspace_root_hash(workspace_root) / "v1"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise CiStorageUnavailable(
            errno=exc.errno or errno.EACCES,
            path=str(base),
            message=(
                f"Cannot create CI state dir at {base} (errno={exc.errno}); "
                f"running as user={os.getenv('USER')}, HOME={home}"
            ),
        ) from exc
    except OSError as exc:
        raise CiStorageUnavailable(
            errno=exc.errno or errno.EACCES,
            path=str(base),
            message=(
                f"Cannot create CI state dir at {base} (errno={exc.errno}, {exc.strerror}); "
                f"HOME={home}"
            ),
        ) from exc
    return base


def _confine(state: Path, name: str) -> Path:
    """Resolve ``name`` under ``state`` and reject path traversal.

    Load-bearing for the storage boundary: an RPC handler must not be able
    to write outside the per-workspace state directory. Rejects ``..``,
    absolute paths, and symlink-traversal targets that escape ``state`` after
    resolution.
    """
    state_real = state.resolve()
    target = (state / name).resolve()
    if target == state_real:
        raise CiStoragePathEscape(
            f"target {target} resolves to the state dir itself"
        )
    if state_real not in target.parents:
        raise CiStoragePathEscape(
            f"path {target} escapes state dir {state_real}"
        )
    return target


def write_snapshot(state: Path, name: str, payload: Any) -> None:
    """Atomic pickle write into ``state/name``.

    Writes to a temp file in the same directory, fsyncs, then ``os.replace``s
    onto the final target. Cleans up the temp file on exception. Pickle
    protocol 5; ``payload`` may be any pickleable structure.
    """
    target = _confine(state, name)
    fd, tmp = tempfile.mkstemp(prefix=f".{Path(name).name}.", suffix=".tmp", dir=state)
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(payload, f, protocol=5)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_snapshot(state: Path, name: str) -> Any | None:
    """Load a pickle snapshot from ``state/name``.

    Returns ``None`` for a missing target. On any pickle/IO corruption,
    logs a warning, unlinks the corrupt file, and returns ``None`` so the
    caller can rebuild from scratch.
    """
    target = _confine(state, name)
    if not target.exists():
        return None
    try:
        with open(target, "rb") as f:
            return pickle.load(f)
    except (EOFError, pickle.UnpicklingError, OSError) as exc:
        logger.warning(
            "ci_storage: corrupt snapshot at %s (%s); unlinking",
            target,
            exc,
        )
        try:
            target.unlink()
        except OSError:
            pass
        return None


# ---------------------------------------------------------------------------
# Phase 3 — SQLite-backed edit-history ledger
# ---------------------------------------------------------------------------

_LEDGER_FILE = "ledger.sqlite3"

_LEDGER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS edits (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    agent_run_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    edit_type TEXT NOT NULL DEFAULT 'edit',
    old_hash TEXT NOT NULL DEFAULT '',
    new_hash TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_edits_file ON edits(file_path);
CREATE INDEX IF NOT EXISTS idx_edits_ts ON edits(ts);
CREATE INDEX IF NOT EXISTS idx_edits_run ON edits(run_id);
CREATE INDEX IF NOT EXISTS idx_edits_agent_run ON edits(run_id, agent_run_id);
"""


def _apply_ledger_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 67108864;")  # 64 MB


def _open_ledger_db(path: Path) -> sqlite3.Connection:
    """Open ``path`` with WAL pragmas, integrity-checking and rotation on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    integrity: str = "ok"
    try:
        _apply_ledger_pragmas(conn)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity = row[0] if row else "unknown"
    except sqlite3.DatabaseError as exc:
        integrity = str(exc) or "database error"
    if integrity != "ok":
        logger.warning(
            "ci_storage: ledger %s failed integrity check (%s); rotating",
            path,
            integrity,
        )
        try:
            conn.close()
        except sqlite3.Error:
            pass
        rotated = path.with_suffix(f".corrupt.{int(time.time())}.sqlite3")
        try:
            path.rename(rotated)
        except OSError:
            try:
                path.unlink()
            except OSError:
                pass
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        _apply_ledger_pragmas(conn)
    conn.executescript(_LEDGER_SCHEMA_SQL)
    return conn


class LedgerStore:
    """SQLite-WAL backed implementation of the EditHistoryLedger interface.

    Used by the daemon-resident :class:`CodeIntelligenceService` so the audit
    log survives daemon restarts. The orchestrator-side default in-memory
    ``EditHistoryLedger`` is unchanged.

    Method signatures match
    :class:`sandbox.code_intelligence.mutations.edit_history_ledger.EditHistoryLedger`
    exactly (verified by interface-parity unit test).
    """

    initialized: bool = True

    def __init__(self, state_dir_path: Path) -> None:
        self._path = state_dir_path / _LEDGER_FILE
        self._lock = threading.Lock()
        self._conn = _open_ledger_db(self._path)

    # ------------------------------------------------------------------ utils

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def _row_to_record(self, row: sqlite3.Row | tuple) -> Any:
        # Imported lazily to avoid a hard cycle: mutations imports nothing
        # from ci_storage but ci_storage forwards records using the public
        # EditRecord dataclass for orchestrator parity.
        from sandbox.code_intelligence.mutations.edit_history_ledger import (
            EditRecord,
        )

        seq, ts, run_id, agent_run_id, task_id, _agent_id, file_path, \
            edit_type, old_hash, new_hash, description = row
        return EditRecord(
            id=int(seq),
            file_path=str(file_path),
            run_id=str(run_id or ""),
            agent_run_id=str(agent_run_id or ""),
            task_id=str(task_id or ""),
            edit_type=str(edit_type or "edit"),
            old_hash=str(old_hash or ""),
            new_hash=str(new_hash or ""),
            description=str(description or ""),
            created_at=datetime.fromtimestamp(float(ts), tz=timezone.utc),
        )

    # ---------------------------------------------------------------- writers

    def record(
        self,
        *,
        run_id: str,
        file_path: str,
        agent_run_id: str = "",
        task_id: str = "",
        edit_type: str = "edit",
        old_hash: str = "",
        new_hash: str = "",
        description: str = "",
    ) -> Any:
        """Persist one edit row and return the resulting EditRecord."""
        ts = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO edits "
                "(ts, run_id, agent_run_id, task_id, agent_id, file_path, "
                " edit_type, old_hash, new_hash, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    run_id or "",
                    agent_run_id or "",
                    task_id or "",
                    "",
                    file_path,
                    edit_type or "edit",
                    old_hash or "",
                    new_hash or "",
                    description or "",
                ),
            )
            seq = int(cur.lastrowid or 0)
        # Return the canonical orchestrator dataclass so callers see the same
        # shape as the in-memory ledger.
        from sandbox.code_intelligence.mutations.edit_history_ledger import (
            EditRecord,
        )

        return EditRecord(
            id=seq,
            file_path=file_path,
            run_id=run_id or "",
            agent_run_id=agent_run_id or "",
            task_id=task_id or "",
            edit_type=edit_type or "edit",
            old_hash=old_hash or "",
            new_hash=new_hash or "",
            description=description or "",
            created_at=datetime.fromtimestamp(ts, tz=timezone.utc),
        )

    # ---------------------------------------------------------------- readers

    def _select_rows(self, sql: str, params: tuple = ()) -> list[Any]:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    def changes_in_scope(
        self,
        run_id: str,
        scope_prefixes: list[str],
        since: float,
    ) -> list[Any]:
        if not scope_prefixes:
            return []
        normalized = [p.rstrip("/") for p in scope_prefixes if p]
        if not normalized:
            return []
        like_clauses = " OR ".join(["file_path LIKE ?"] * len(normalized))
        params = [run_id, float(since), *(f"{prefix}%" for prefix in normalized)]
        sql = (
            "SELECT seq, ts, run_id, agent_run_id, task_id, agent_id, file_path, "
            "edit_type, old_hash, new_hash, description "
            "FROM edits "
            f"WHERE run_id = ? AND ts > ? AND ({like_clauses}) "
            "ORDER BY seq"
        )
        return self._select_rows(sql, tuple(params))

    def external_changes_in_scope(
        self,
        run_id: str,
        scope_prefixes: list[str],
        since: float,
        exclude_run_id: str | None = None,
    ) -> list[Any]:
        rows = self.changes_in_scope(run_id, scope_prefixes, since)
        if exclude_run_id:
            rows = [r for r in rows if r.agent_run_id != exclude_run_id]
        return rows

    def changes_since(
        self,
        since: float,
        run_id: str | None = None,
    ) -> list[Any]:
        if run_id is None:
            sql = (
                "SELECT seq, ts, run_id, agent_run_id, task_id, agent_id, file_path, "
                "edit_type, old_hash, new_hash, description "
                "FROM edits WHERE ts > ? ORDER BY seq"
            )
            return self._select_rows(sql, (float(since),))
        sql = (
            "SELECT seq, ts, run_id, agent_run_id, task_id, agent_id, file_path, "
            "edit_type, old_hash, new_hash, description "
            "FROM edits WHERE ts > ? AND run_id = ? ORDER BY seq"
        )
        return self._select_rows(sql, (float(since), run_id))

    def recent_edits(
        self,
        seconds: float = 60.0,
        run_id: str | None = None,
    ) -> list[Any]:
        return self.changes_since(time.time() - seconds, run_id=run_id)

    def hotspots(
        self,
        limit: int = 10,
        run_id: str | None = None,
    ) -> list[tuple[str, int]]:
        if run_id is None:
            sql = "SELECT file_path FROM edits"
            params: tuple = ()
        else:
            sql = "SELECT file_path FROM edits WHERE run_id = ?"
            params = (run_id,)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        counter: Counter[str] = Counter(str(r[0]) for r in rows)
        return counter.most_common(limit)

    def who_changed(
        self,
        file_path: str,
        run_id: str | None = None,
    ) -> list[Any]:
        if run_id is None:
            sql = (
                "SELECT seq, ts, run_id, agent_run_id, task_id, agent_id, file_path, "
                "edit_type, old_hash, new_hash, description "
                "FROM edits WHERE file_path = ? ORDER BY seq"
            )
            return self._select_rows(sql, (file_path,))
        sql = (
            "SELECT seq, ts, run_id, agent_run_id, task_id, agent_id, file_path, "
            "edit_type, old_hash, new_hash, description "
            "FROM edits WHERE file_path = ? AND run_id = ? ORDER BY seq"
        )
        return self._select_rows(sql, (file_path, run_id))

    def changes_by_agent_run(
        self,
        run_id: str,
        agent_run_id: str,
    ) -> list[Any]:
        if not agent_run_id:
            return []
        sql = (
            "SELECT seq, ts, run_id, agent_run_id, task_id, agent_id, file_path, "
            "edit_type, old_hash, new_hash, description "
            "FROM edits WHERE run_id = ? AND agent_run_id = ? ORDER BY seq"
        )
        return self._select_rows(sql, (run_id, agent_run_id))

    def contention_hotspots(
        self,
        scope_prefixes: list[str] | None = None,
        limit: int = 10,
        days: int = 7,
        run_id: str | None = None,
    ) -> list[Any]:
        from sandbox.code_intelligence.mutations.edit_history_ledger import (
            ContentionHotspot,
        )

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        clauses = ["ts > ?"]
        params: list[Any] = [cutoff]
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        normalized = [p.rstrip("/") for p in (scope_prefixes or []) if p]
        if normalized:
            like_clauses = " OR ".join(["file_path LIKE ?"] * len(normalized))
            clauses.append(f"({like_clauses})")
            params.extend(f"{prefix}%" for prefix in normalized)
        where = " AND ".join(clauses)
        sql = (
            "SELECT seq, ts, run_id, agent_run_id, task_id, agent_id, file_path, "
            "edit_type, old_hash, new_hash, description "
            f"FROM edits WHERE {where} ORDER BY seq"
        )
        records = self._select_rows(sql, tuple(params))

        contributors_by_file: dict[str, set[str]] = {}
        counts: Counter[str] = Counter()
        for r in records:
            contributor = r.task_id or r.agent_run_id
            if not contributor:
                contributor = f"edit:{r.id}"
            contributors_by_file.setdefault(r.file_path, set()).add(contributor)
            counts[r.file_path] += 1
        results = [
            ContentionHotspot(
                file_path=fp,
                contributor_count=len(contributors),
                edit_count=counts[fp],
            )
            for fp, contributors in contributors_by_file.items()
            if len(contributors) > 1
        ]
        results.sort(key=lambda h: (-h.contributor_count, -h.edit_count))
        return results[:limit]


# ---------------------------------------------------------------------------
# Phase 3.5 — SQLite-backed symbol index storage
# ---------------------------------------------------------------------------

_INDEX_FILE = "index.sqlite3"

_INDEX_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS index_files (
    file_path TEXT PRIMARY KEY,
    generation INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL,
    symbols_blob BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_index_files_generation ON index_files(generation);
"""


def _apply_index_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 67108864;")  # 64 MB


def _open_index_db(path: Path) -> sqlite3.Connection:
    """Open ``path`` with WAL pragmas; integrity-check + rotate on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    integrity: str = "ok"
    try:
        _apply_index_pragmas(conn)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity = row[0] if row else "unknown"
    except sqlite3.DatabaseError as exc:
        integrity = str(exc) or "database error"
    if integrity != "ok":
        logger.warning(
            "ci_storage: index %s failed integrity check (%s); rotating",
            path,
            integrity,
        )
        try:
            conn.close()
        except sqlite3.Error:
            pass
        rotated = path.with_suffix(f".corrupt.{int(time.time())}.sqlite3")
        try:
            path.rename(rotated)
        except OSError:
            try:
                path.unlink()
            except OSError:
                pass
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        _apply_index_pragmas(conn)
    conn.executescript(_INDEX_SCHEMA_SQL)
    return conn


def _encode_symbols(symbols: list[Any]) -> bytes:
    """msgpack-encode a list of SymbolInfo dataclasses to a single blob."""
    import msgpack

    payload = []
    for sym in symbols:
        payload.append(
            {
                "name": str(getattr(sym, "name", "")),
                "kind": str(getattr(getattr(sym, "kind", ""), "value", getattr(sym, "kind", ""))),
                "file_path": str(getattr(sym, "file_path", "")),
                "line": int(getattr(sym, "line", 0)),
                "end_line": getattr(sym, "end_line", None),
                "character": int(getattr(sym, "character", 0)),
                "signature": str(getattr(sym, "signature", "")),
                "docstring": str(getattr(sym, "docstring", "")),
                "container": str(getattr(sym, "container", "")),
            }
        )
    return msgpack.packb(payload, use_bin_type=True)


def _decode_symbols(blob: bytes) -> list[Any]:
    """msgpack-decode a blob back into SymbolInfo dataclasses."""
    import msgpack

    from sandbox.code_intelligence.core.types import SymbolInfo, SymbolKind

    if not blob:
        return []
    payload = msgpack.unpackb(blob, raw=False)
    out: list[Any] = []
    for d in payload:
        kind_raw = d.get("kind", "unknown")
        try:
            kind = SymbolKind(kind_raw)
        except ValueError:
            kind = SymbolKind.UNKNOWN
        out.append(
            SymbolInfo(
                name=str(d.get("name", "")),
                kind=kind,
                file_path=str(d.get("file_path", "")),
                line=int(d.get("line", 0)),
                end_line=d.get("end_line"),
                character=int(d.get("character", 0)),
                signature=str(d.get("signature", "")),
                docstring=str(d.get("docstring", "")),
                container=str(d.get("container", "")),
            )
        )
    return out


class IndexStore:
    """SQLite-WAL backed symbol-index storage.

    One row per file. ``symbols_blob`` is a msgpack-encoded list of
    :class:`SymbolInfo`. ``bulk_replace`` is atomic; ``refresh_file`` /
    ``delete_file`` touch a single PK. ``query_by_substring`` is a
    parity-preserving linear scan (matches today's in-memory ``find``).

    Phase 3.5 swaps the orchestrator-side pickle ``index.snapshot`` for
    this store via :func:`migrate_pickle_to_sqlite`.
    """

    def __init__(self, state_dir_path: Path) -> None:
        self._path = state_dir_path / _INDEX_FILE
        self._lock = threading.Lock()
        self._conn = _open_index_db(self._path)
        self._generation = self._read_max_generation()

    # ------------------------------------------------------------------ utils

    @property
    def path(self) -> Path:
        return self._path

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def _read_max_generation(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM index_files"
            ).fetchone()
            return int(row[0] if row else 0)

    # ---------------------------------------------------------------- writers

    def bulk_replace(self, snapshot: dict[str, list[Any]]) -> int:
        """Atomic full replacement: BEGIN; DELETE; INSERT…; COMMIT.

        Returns the new generation. All rows share the same generation so a
        partial commit cannot leave stale entries.
        """
        now = time.time()
        with self._lock:
            self._generation += 1
            gen = self._generation
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("DELETE FROM index_files")
                self._conn.executemany(
                    "INSERT INTO index_files "
                    "(file_path, generation, indexed_at, symbols_blob) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        (str(fp), gen, now, _encode_symbols(syms))
                        for fp, syms in snapshot.items()
                    ],
                )
                self._conn.execute("COMMIT")
            except sqlite3.Error:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        return gen

    def refresh_file(self, file_path: str, symbols: list[Any]) -> int:
        """INSERT OR REPLACE one row; returns the new generation."""
        now = time.time()
        blob = _encode_symbols(symbols)
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._conn.execute(
                "INSERT OR REPLACE INTO index_files "
                "(file_path, generation, indexed_at, symbols_blob) "
                "VALUES (?, ?, ?, ?)",
                (str(file_path), gen, now, blob),
            )
        return gen

    def delete_file(self, file_path: str) -> int:
        """DELETE one row; returns the new generation."""
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._conn.execute(
                "DELETE FROM index_files WHERE file_path = ?",
                (str(file_path),),
            )
        return gen

    # ---------------------------------------------------------------- readers

    def file_symbols(self, file_path: str) -> list[Any]:
        """PK lookup for a single file's symbols."""
        with self._lock:
            row = self._conn.execute(
                "SELECT symbols_blob FROM index_files WHERE file_path = ?",
                (str(file_path),),
            ).fetchone()
        if not row:
            return []
        return _decode_symbols(row[0])

    def indexed_paths(self) -> list[str]:
        """All indexed file paths, sorted."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path FROM index_files ORDER BY file_path"
            ).fetchall()
        return [str(r[0]) for r in rows]

    def all_symbols(self) -> list[Any]:
        """Materialize every symbol across every file."""
        out: list[Any] = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbols_blob FROM index_files"
            ).fetchall()
        for row in rows:
            out.extend(_decode_symbols(row[0]))
        return out

    def size(self) -> int:
        """Total symbol count across all indexed files."""
        return len(self.all_symbols())

    def indexed_files(self) -> int:
        """Number of files in the index."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM index_files"
            ).fetchone()
        return int(row[0] if row else 0)

    def query_by_substring(self, needle: str, kind: Any = None) -> list[Any]:
        """Naive linear scan — parity with in-memory ``SymbolIndex.find``."""
        n = (needle or "").lower().strip()
        if not n:
            return []
        out: list[Any] = []
        for sym in self.all_symbols():
            if n in sym.name.lower() and (kind is None or sym.kind == kind):
                out.append(sym)
        return out


def migrate_pickle_to_sqlite(state: Path) -> int:
    """One-shot pickle ``index.snapshot`` → SQLite ``index.sqlite3`` migration.

    Idempotent. Returns the number of files migrated, or 0 if no pickle was
    present (or it was corrupt). Pickle is unlinked on success.
    """
    pickle_path = state / "index.snapshot"
    if not pickle_path.exists():
        return 0
    snapshot = read_snapshot(state, "index.snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        # Either corrupt (already unlinked by read_snapshot) or empty.
        try:
            pickle_path.unlink()
        except OSError:
            pass
        return 0
    store = IndexStore(state_dir_path=state)
    try:
        store.bulk_replace(snapshot)
    finally:
        store.close()
    try:
        pickle_path.unlink()
    except OSError:
        pass
    logger.info(
        "ci_storage: migrated %d files from pickle to sqlite", len(snapshot)
    )
    return len(snapshot)
