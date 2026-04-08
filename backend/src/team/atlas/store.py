"""Transactional CRUD for the Project Atlas.

``AtlasStore`` is the only writer for the ``project_atlas`` and
``project_atlas_chunks`` tables. Every mutation runs inside a single
SQLAlchemy transaction so concurrent ``atlas_builder`` / ``atlas_refresher``
runs cannot leave the atlas in a torn state: the last writer wins
deterministically on the ``(project_key, subsystem)`` unique constraint.

Chunks are returned as :class:`AtlasChunk` dataclasses so callers never
touch the ORM record objects. The brief body inside ``AtlasChunk.brief``
is identical in shape to a Phase 1 scout artifact, which lets
consumers re-use the same ``render_briefings`` path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from team.atlas.model import ProjectAtlasChunkRecord, ProjectAtlasRecord

logger = logging.getLogger(__name__)


@dataclass
class AtlasChunk:
    """One cached scout brief, keyed by ``(project_key, subsystem)``.

    ``content_hashes`` maps file paths under the chunk's scope to their
    16-char sha256 prefix at write time. Used as a cold-start fallback
    for freshness checks when the in-memory ledger is empty (fresh
    process, new session).
    """

    subsystem: str
    brief: dict[str, Any]
    updated_at: datetime | None = None
    content_hashes: dict[str, str] = field(default_factory=dict)
    symbol_ids: list[str] = field(default_factory=list)


@dataclass
class AtlasHeader:
    """Header metadata for a project's atlas. Returned by :meth:`AtlasStore.get_atlas`."""

    project_key: str
    repo_root: str
    updated_at: datetime
    subsystems: list[str] = field(default_factory=list)


class AtlasStore:
    """SQLAlchemy-backed CRUD for the project atlas.

    Initialised once by the application factory with a session factory;
    tests pass an in-memory SQLite factory via the same ``initialize``
    contract used by :class:`team.persistence.store.TeamDefinitionStore`.
    """

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
    ) -> None:
        """Upsert a header + N chunks in one transaction.

        Concurrent builder/refresher runs are safe because each call runs
        inside its own session and the unique constraint on
        ``(project_key, subsystem)`` makes the write idempotent: the last
        writer wins deterministically instead of corrupting state.
        """
        if not project_key:
            raise ValueError("project_key must be non-empty")
        with self._sf() as db:
            header = (
                db.query(ProjectAtlasRecord)
                .filter(ProjectAtlasRecord.project_key == project_key)
                .first()
            )
            if header is None:
                header = ProjectAtlasRecord(
                    project_key=project_key,
                    repo_root=repo_root,
                )
                db.add(header)
            else:
                header.repo_root = repo_root

            for chunk in chunks:
                if not chunk.subsystem:
                    raise ValueError("chunk.subsystem must be non-empty")
                existing = (
                    db.query(ProjectAtlasChunkRecord)
                    .filter(
                        ProjectAtlasChunkRecord.project_key == project_key,
                        ProjectAtlasChunkRecord.subsystem == chunk.subsystem,
                    )
                    .first()
                )
                if existing is None:
                    db.add(
                        ProjectAtlasChunkRecord(
                            project_key=project_key,
                            subsystem=chunk.subsystem,
                            brief_json=dict(chunk.brief),
                            content_hashes_json=dict(chunk.content_hashes),
                            symbol_ids_json=list(chunk.symbol_ids),
                        )
                    )
                else:
                    existing.brief_json = dict(chunk.brief)
                    existing.content_hashes_json = dict(chunk.content_hashes)
                    existing.symbol_ids_json = list(chunk.symbol_ids)
            db.commit()
            logger.debug(
                "atlas upsert: project=%s chunks=%d",
                project_key,
                len(chunks),
            )

    # ---- reads -----------------------------------------------------------

    def get_atlas(self, project_key: str) -> AtlasHeader | None:
        """Return the header + subsystem list for *project_key*, or None."""
        with self._sf() as db:
            header = (
                db.query(ProjectAtlasRecord)
                .filter(ProjectAtlasRecord.project_key == project_key)
                .first()
            )
            if header is None:
                return None
            subsystems = [
                row.subsystem
                for row in db.query(ProjectAtlasChunkRecord)
                .filter(ProjectAtlasChunkRecord.project_key == project_key)
                .order_by(ProjectAtlasChunkRecord.subsystem)
                .all()
            ]
            return AtlasHeader(
                project_key=header.project_key,
                repo_root=header.repo_root,
                updated_at=header.updated_at,
                subsystems=subsystems,
            )

    def get_chunk(self, project_key: str, subsystem: str) -> AtlasChunk | None:
        """Return a single chunk by ``(project_key, subsystem)``, or None."""
        with self._sf() as db:
            row = (
                db.query(ProjectAtlasChunkRecord)
                .filter(
                    ProjectAtlasChunkRecord.project_key == project_key,
                    ProjectAtlasChunkRecord.subsystem == subsystem,
                )
                .first()
            )
            if row is None:
                return None
            return AtlasChunk(
                subsystem=row.subsystem,
                brief=dict(row.brief_json or {}),
                updated_at=row.updated_at,
                content_hashes=dict(row.content_hashes_json or {}),
                symbol_ids=list(row.symbol_ids_json or []),
            )

    def list_chunks(self, project_key: str) -> list[AtlasChunk]:
        """Return every chunk for a project, ordered by subsystem."""
        with self._sf() as db:
            rows = (
                db.query(ProjectAtlasChunkRecord)
                .filter(ProjectAtlasChunkRecord.project_key == project_key)
                .order_by(ProjectAtlasChunkRecord.subsystem)
                .all()
            )
            return [
                AtlasChunk(
                    subsystem=r.subsystem,
                    brief=dict(r.brief_json or {}),
                    updated_at=r.updated_at,
                    content_hashes=dict(r.content_hashes_json or {}),
                )
                for r in rows
            ]


# Module-level singleton, initialised by the application factory at
# bootstrap and consumed by the atlas tools / posthooks. Tests that need
# a fresh store can instantiate :class:`AtlasStore` directly or inject an
# override via tool execution metadata.
_default_store: AtlasStore = AtlasStore()


def get_default_store() -> AtlasStore:
    """Return the process-wide default atlas store."""
    return _default_store
