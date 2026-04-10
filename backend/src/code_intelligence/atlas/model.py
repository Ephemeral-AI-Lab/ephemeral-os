"""Project Atlas persistence ORM records.

Two tables, one per concern:

- ``project_atlas`` — one row per project. Tracks the repo root path.
- ``project_atlas_chunks`` — one row per ``(project_key, subsystem)`` pair
  holding the scout brief body, its per-file content-hash snapshot, a
  monotonic ``brief_version`` (used for version-guarded upserts under
  concurrent writers), and the pre-read ``snapshot_time`` used as the
  ledger cutoff for freshness checks.

Both tables are registered on the shared :class:`db.base.Base` so
``Base.metadata.create_all`` picks them up the same way the rest of the
application models do. No domain logic lives here — that belongs in
:mod:`code_intelligence.atlas.store`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# JSON on SQLite (tests), JSONB on Postgres (prod) — one column type, two
# dialects. Queryability + indexability come for free in Postgres without
# breaking the in-memory SQLite tests.
_JSON_COL = JSON().with_variant(JSONB(), "postgresql")


class ProjectAtlasRecord(Base):
    """Header row for a project's atlas.

    ``project_key`` is a stable identity derived from the repo root (see
    :mod:`code_intelligence.atlas.identity`). Freshness is computed per-chunk via the
    edit ledger and stored content hashes — this row is purely a pointer.
    """

    __tablename__ = "project_atlas"

    project_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    repo_root: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ProjectAtlasRecord key={self.project_key!r}>"


class ProjectAtlasChunkRecord(Base):
    """One scout brief cached under ``(project_key, subsystem)``.

    - ``brief_json`` — scout brief body (same shape as the Phase 1 scout
      artifact).
    - ``content_hashes_json`` — ``path → sha256[:16]`` map of every file
      under the chunk's scope at write time.
    - ``snapshot_time`` — the Unix timestamp captured *before* the scout
      read files. Used as the ledger cutoff in freshness checks so edits
      that landed between "files read" and "row committed" are not
      silently missed.
    - ``brief_version`` — a monotonic version stamp (``time.time_ns()``
      by default). Updates are conditional on the incoming version being
      strictly greater than the stored version, so concurrent writers
      cannot let a slow, stale writer overwrite a fresh one.
    """

    __tablename__ = "project_atlas_chunks"

    project_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    subsystem: Mapped[str] = mapped_column(String(512), primary_key=True)
    brief_json: Mapped[dict[str, Any]] = mapped_column(_JSON_COL)
    content_hashes_json: Mapped[dict[str, str]] = mapped_column(
        _JSON_COL, default=dict, nullable=False
    )
    symbol_ids_json: Mapped[list[str]] = mapped_column(
        _JSON_COL, default=list, nullable=False
    )
    snapshot_time: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    brief_version: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectAtlasChunkRecord key={self.project_key!r} "
            f"sub={self.subsystem!r} v={self.brief_version}>"
        )
