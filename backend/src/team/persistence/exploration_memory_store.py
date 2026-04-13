"""ExplorationMemoryStore — async PG persistence for exploration cache.

Follows the existing Store pattern (NoteStore, etc.) with async_sessionmaker.
Backs the in-memory ExplorationMemory singleton with durable cross-process storage.

Table schema: see schema.sql ``exploration_memory`` table.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Text, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.stores.base import AsyncStoreMixin

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# ORM Model
# ---------------------------------------------------------------------------


class ExplorationMemoryRecord(Base):
    """Durable exploration cache entry."""

    __tablename__ = "exploration_memory"

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_paths: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ExplorationMemoryRecord key={self.cache_key!r}>"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ExplorationMemoryStore(AsyncStoreMixin):
    """Async PG persistence for exploration cache. Follows NoteStore pattern."""

    async def get(self, cache_key: str) -> list[dict[str, Any]] | None:
        """Fetch cached notes by key. Returns None on miss."""
        async with self._sf() as db:
            stmt = select(ExplorationMemoryRecord).where(
                ExplorationMemoryRecord.cache_key == cache_key
            )
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()
            if record is None:
                return None
            # Touch accessed_at
            record.accessed_at = _utcnow()
            await db.commit()
            return record.notes  # type: ignore[return-value]

    async def put(
        self,
        cache_key: str,
        scope_paths: list[str],
        content_hash: str,
        notes: list[dict[str, Any]],
    ) -> None:
        """Upsert a cache entry."""
        async with self._sf() as db:
            stmt = select(ExplorationMemoryRecord).where(
                ExplorationMemoryRecord.cache_key == cache_key
            )
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()
            now = _utcnow()
            if record is not None:
                record.scope_paths = scope_paths
                record.content_hash = content_hash
                record.notes = notes  # type: ignore[assignment]
                record.accessed_at = now
            else:
                record = ExplorationMemoryRecord(
                    cache_key=cache_key,
                    scope_paths=scope_paths,
                    content_hash=content_hash,
                    notes=notes,  # type: ignore[arg-type]
                    created_at=now,
                    accessed_at=now,
                )
                db.add(record)
            await db.commit()

    async def evict_stale(self, max_age_hours: int = 72) -> int:
        """Delete entries not accessed in *max_age_hours*. Returns count deleted."""
        from datetime import timedelta

        from sqlalchemy import delete

        cutoff = _utcnow() - timedelta(hours=max_age_hours)
        async with self._sf() as db:
            stmt = delete(ExplorationMemoryRecord).where(
                ExplorationMemoryRecord.accessed_at < cutoff
            )
            result = await db.execute(stmt)
            await db.commit()
            return result.rowcount  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Null fallback (no-PG / tests)
# ---------------------------------------------------------------------------


class NullExplorationMemoryStore:
    """No-op store for when PostgreSQL is unavailable."""

    initialized: bool = False

    async def get(self, cache_key: str) -> None:
        return None

    async def put(self, **kwargs: Any) -> None:
        pass

    async def evict_stale(self, **kwargs: Any) -> int:
        return 0
