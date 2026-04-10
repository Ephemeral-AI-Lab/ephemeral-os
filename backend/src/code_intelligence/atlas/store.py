"""Transactional CRUD for the Project Atlas.

``AtlasStore`` is the only writer for the ``project_atlas`` and
``project_atlas_chunks`` tables.

Concurrency model
-----------------
Every chunk mutation is **version-guarded**: writes are accepted only
when the incoming ``brief_version`` is strictly greater than the stored
version. Under concurrent Atlas writes this means a slow, stale writer
cannot overwrite a fresh one — the
conditional ``UPDATE ... WHERE brief_version < :new_version`` turns
stale writes into no-ops instead of corrupting state. Inserts race
against the ``(project_key, subsystem)`` PK and recover via a nested
savepoint so a concurrent insert is converted into a version-guarded
update without blowing away the enclosing transaction.

Chunks are returned as :class:`AtlasChunk` dataclasses so callers never
touch the ORM record objects. The brief body inside ``AtlasChunk.brief``
is identical in shape to a Phase 1 scout artifact.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from team.atlas.model import ProjectAtlasChunkRecord, ProjectAtlasRecord

logger = logging.getLogger(__name__)


# Version stamps are strictly increasing 63-bit integers so they fit in
# SQLite INTEGER / Postgres BIGINT. We seed from ``time.time_ns()`` so
# versions are roughly wall-clock-ordered across process restarts, but
# under concurrency we advance a lock-protected counter — two callers
# in the same ns bucket still get strictly distinct values.
_version_lock = threading.Lock()
_last_version = time.time_ns()


def _fresh_version() -> int:
    """Monotonic unique version stamp — collision-free under concurrency."""
    global _last_version
    with _version_lock:
        now = time.time_ns()
        _last_version = max(_last_version + 1, now)
        return _last_version


@dataclass
class AtlasChunk:
    """One cached scout brief, keyed by ``(project_key, subsystem)``.

    - ``content_hashes`` maps file paths under the chunk's scope to
      their 16-char sha256 prefix at write time.
    - ``snapshot_time`` is the wall-clock (seconds) captured *before*
      the scout started reading files; it is the ledger cutoff used by
      freshness checks and is persisted verbatim.
    - ``brief_version`` is a monotonic stamp. Callers that do not set
      one get a fresh ``time.time_ns()`` at construction, which makes
      every new ``AtlasChunk`` instance sortable against older rows.
    """

    subsystem: str
    brief: dict[str, Any]
    updated_at: datetime | None = None
    content_hashes: dict[str, str] = field(default_factory=dict)
    symbol_ids: list[str] = field(default_factory=list)
    repo_root: str = ""
    snapshot_time: float = 0.0
    brief_version: int = field(default_factory=_fresh_version)


class AtlasStore:
    """SQLAlchemy-backed CRUD for the project atlas."""

    def __init__(self) -> None:
        self._session_factory: sessionmaker[Session] | None = None

    def initialize(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        logger.info("AtlasStore initialised")

    def is_initialised(self) -> bool:
        return self._session_factory is not None

    @property
    def _sf(self) -> sessionmaker[Session]:
        assert self._session_factory is not None, "AtlasStore not initialised"
        return self._session_factory

    # ---- writes ----------------------------------------------------------

    def upsert_chunks(
        self,
        *,
        project_key: str,
        repo_root: str,
        chunks: list[AtlasChunk],
    ) -> int:
        """Upsert a header + N chunks in one transaction.

        Returns the number of chunks whose write was actually applied —
        stale writes (``brief_version <= existing``) are silently skipped
        and do **not** count toward the return value.
        """
        if not project_key:
            raise ValueError("project_key must be non-empty")

        applied = 0
        with self._sf() as db:
            self._upsert_header(db, project_key, repo_root)

            for chunk in chunks:
                if not chunk.subsystem:
                    raise ValueError("chunk.subsystem must be non-empty")
                if self._apply_chunk(db, project_key, chunk):
                    applied += 1

            db.commit()
            logger.debug(
                "atlas upsert: project=%s applied=%d skipped=%d",
                project_key,
                applied,
                len(chunks) - applied,
            )
        return applied

    def _apply_chunk(
        self,
        db: Session,
        project_key: str,
        chunk: AtlasChunk,
    ) -> bool:
        """Version-guarded upsert for one chunk. Returns True if applied."""
        values = {
            "brief_json": dict(chunk.brief),
            "content_hashes_json": dict(chunk.content_hashes),
            "symbol_ids_json": list(chunk.symbol_ids),
            "snapshot_time": float(chunk.snapshot_time or 0.0),
            "brief_version": int(chunk.brief_version),
        }
        # Conditional update — only overwrites older versions. rowcount==0
        # means either the row doesn't exist, or an equal/newer row is
        # already persisted.
        stmt = (
            update(ProjectAtlasChunkRecord)
            .where(ProjectAtlasChunkRecord.project_key == project_key)
            .where(ProjectAtlasChunkRecord.subsystem == chunk.subsystem)
            .where(ProjectAtlasChunkRecord.brief_version < chunk.brief_version)
            .values(**values)
        )
        result = db.execute(stmt)
        if result.rowcount and result.rowcount > 0:
            return True

        # rowcount==0 — distinguish "not there yet" from "stale write".
        existing = db.get(
            ProjectAtlasChunkRecord, (project_key, chunk.subsystem)
        )
        if existing is not None:
            # Existing row has brief_version >= ours → stale write, skip.
            logger.debug(
                "atlas upsert: skipping stale write for %s (incoming=%d stored=%d)",
                chunk.subsystem,
                chunk.brief_version,
                existing.brief_version,
            )
            return False

        # Insert in a savepoint so a concurrent inserter doesn't abort
        # the whole outer transaction.
        try:
            with db.begin_nested():
                db.add(
                    ProjectAtlasChunkRecord(
                        project_key=project_key,
                        subsystem=chunk.subsystem,
                        **values,
                    )
                )
                db.flush()
            return True
        except IntegrityError:
            # Someone inserted between our UPDATE and our INSERT. Retry
            # the conditional update — if their version is newer, we
            # still skip; if ours is newer, we win. We must re-execute
            # the statement to get a fresh Result — the original `stmt`
            # is a SQL expression and has no rowcount.
            retry = db.execute(stmt)
            return bool(retry.rowcount and retry.rowcount > 0)

    def _upsert_header(self, db: Session, project_key: str, repo_root: str) -> None:
        """Insert or update the project header without losing insert races."""
        header = db.get(ProjectAtlasRecord, project_key)
        if header is not None:
            header.repo_root = repo_root
            return
        try:
            with db.begin_nested():
                db.add(ProjectAtlasRecord(project_key=project_key, repo_root=repo_root))
                db.flush()
        except IntegrityError:
            existing = db.get(ProjectAtlasRecord, project_key)
            if existing is None:
                raise
            existing.repo_root = repo_root

    # ---- reads -----------------------------------------------------------

    def get_chunk(self, project_key: str, subsystem: str) -> AtlasChunk | None:
        """Return a single chunk by ``(project_key, subsystem)``, or None."""
        results = self.get_chunks(project_key, [subsystem])
        return results[0] if results else None

    def has_chunks(self, project_key: str) -> bool:
        """Return True when *project_key* has at least one persisted chunk."""
        if not project_key:
            return False
        with self._sf() as db:
            row = db.execute(
                select(ProjectAtlasChunkRecord.subsystem)
                .where(ProjectAtlasChunkRecord.project_key == project_key)
                .limit(1)
            ).first()
        return row is not None

    def list_subsystems(self, project_key: str) -> list[str]:
        """Return every persisted subsystem key for *project_key*."""
        if not project_key:
            return []
        with self._sf() as db:
            rows = db.execute(
                select(ProjectAtlasChunkRecord.subsystem)
                .where(ProjectAtlasChunkRecord.project_key == project_key)
                .order_by(ProjectAtlasChunkRecord.subsystem)
            ).all()
        return [str(row[0]) for row in rows if row and row[0]]

    def get_chunks(
        self, project_key: str, subsystems: Iterable[str]
    ) -> list[AtlasChunk]:
        """Batch read: one query for N chunks + one for the header.

        Preserves caller order; missing chunks are simply omitted. This
        is the path planners should prefer when looking up multiple
        subsystems — it amortises the header fetch and avoids N×2
        round-trips of ``get_chunk``.
        """
        subs = [s for s in subsystems if s]
        if not subs:
            return []
        with self._sf() as db:
            header = db.get(ProjectAtlasRecord, project_key)
            repo_root = header.repo_root if header else ""

            rows = db.execute(
                select(ProjectAtlasChunkRecord)
                .where(ProjectAtlasChunkRecord.project_key == project_key)
                .where(ProjectAtlasChunkRecord.subsystem.in_(subs))
            ).scalars().all()

            by_key = {row.subsystem: row for row in rows}
            out: list[AtlasChunk] = []
            for sub in subs:
                row = by_key.get(sub)
                if row is None:
                    continue
                out.append(
                    AtlasChunk(
                        subsystem=row.subsystem,
                        brief=dict(row.brief_json or {}),
                        updated_at=row.updated_at,
                        content_hashes=dict(row.content_hashes_json or {}),
                        symbol_ids=list(row.symbol_ids_json or []),
                        repo_root=repo_root,
                        snapshot_time=float(row.snapshot_time or 0.0),
                        brief_version=int(row.brief_version or 0),
                    )
                )
            return out


# Module-level singleton, initialised by the application factory at
# bootstrap and consumed by the atlas tools / posthooks.
_default_store: AtlasStore = AtlasStore()


def get_default_store() -> AtlasStore:
    """Return the process-wide default atlas store."""
    return _default_store
