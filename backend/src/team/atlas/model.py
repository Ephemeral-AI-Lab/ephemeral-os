"""Project Atlas persistence ORM records.

Two tables, one per concern:

- ``project_atlas`` — one row per project. Tracks the repo root path.
- ``project_atlas_chunks`` — one row per ``(project_key, subsystem)`` pair
  holding the scout brief body and its per-file content-hash snapshot.

Both tables are registered on the shared :class:`db.base.Base` so
``Base.metadata.create_all`` picks them up the same way the rest of the
application models do. No domain logic lives here — that belongs in
:mod:`team.atlas.store`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectAtlasRecord(Base):
    """Header row for a project's atlas.

    ``project_key`` is a stable identity derived from the repo root (see
    :mod:`team.atlas.identity`). Freshness is computed per-chunk via the
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

    ``brief_json`` stores the full brief body — same shape as the scout
    posthook artifact. ``content_hashes_json`` stores a ``path → sha256[:16]``
    map of every file under the chunk's scope at write time, used by
    :mod:`team.atlas.freshness` for cold-start staleness checks.
    """

    __tablename__ = "project_atlas_chunks"

    project_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    subsystem: Mapped[str] = mapped_column(String(512), primary_key=True)
    brief_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hashes_json: Mapped[dict[str, str]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    symbol_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectAtlasChunkRecord key={self.project_key!r} "
            f"sub={self.subsystem!r}>"
        )
