"""CheckpointStore — durable checkpoint persistence for crash recovery.

Stores TeamRunCheckpoint snapshots in PostgreSQL so that checkpoints
survive process restarts. The in-memory deque in Dispatcher remains
the hot-path read cache; this store handles durability.

Uses async_sessionmaker, matching the TaskCenter pattern.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, delete, select
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


class CheckpointRecord(Base):
    """Durable record of a TeamRunCheckpoint."""

    __tablename__ = "team_run_checkpoints"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    tasks: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ready_queue_order: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list
    )
    budget_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    project_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<CheckpointRecord {self.id!r} "
            f"run={self.team_run_id!r} seq={self.sequence}>"
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class CheckpointStore(AsyncStoreMixin):
    """Async checkpoint persistence. Follows TaskCenter pattern."""

    async def save(self, checkpoint: Any) -> None:
        """Persist a TeamRunCheckpoint snapshot.

        Serializes tasks (dict[str, Task]) and budget_state via
        dataclasses.asdict(). project_context is stored as-is (must be
        JSON-serializable or None).
        """
        tasks_json = {
            task_id: asdict(task)
            for task_id, task in checkpoint.tasks.items()
        }
        # Convert non-serializable fields
        for task_data in tasks_json.values():
            for field in ("created_at", "started_at", "finished_at"):
                val = task_data.get(field)
                if isinstance(val, datetime):
                    task_data[field] = val.isoformat()
            if "status" in task_data and hasattr(task_data["status"], "value"):
                task_data["status"] = task_data["status"].value

        record = CheckpointRecord(
            id=checkpoint.id,
            team_run_id=checkpoint.team_run_id,
            sequence=checkpoint.sequence,
            taken_at=checkpoint.taken_at,
            label=checkpoint.label,
            tasks=tasks_json,
            ready_queue_order=list(checkpoint.ready_queue_order),
            budget_state=asdict(checkpoint.budget_state),
            project_context=self._safe_json(checkpoint.project_context),
        )
        async with self._sf() as db:
            await db.merge(record)
            await db.commit()

    async def load_latest(self, team_run_id: str) -> CheckpointRecord | None:
        """Load the most recent checkpoint for a run."""
        async with self._sf() as db:
            stmt = (
                select(CheckpointRecord)
                .where(CheckpointRecord.team_run_id == team_run_id)
                .order_by(CheckpointRecord.sequence.desc())
                .limit(1)
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def load_by_id(
        self, checkpoint_id: str, team_run_id: str
    ) -> CheckpointRecord | None:
        """Load a specific checkpoint by ID."""
        async with self._sf() as db:
            stmt = select(CheckpointRecord).where(
                CheckpointRecord.id == checkpoint_id,
                CheckpointRecord.team_run_id == team_run_id,
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def list_for_run(self, team_run_id: str) -> list[CheckpointRecord]:
        """List all checkpoints for a run, ordered by sequence."""
        async with self._sf() as db:
            stmt = (
                select(CheckpointRecord)
                .where(CheckpointRecord.team_run_id == team_run_id)
                .order_by(CheckpointRecord.sequence.asc())
            )
            return list((await db.execute(stmt)).scalars().all())

    async def delete_for_run(self, team_run_id: str) -> int:
        """Delete all checkpoints for a run. Returns count deleted."""
        async with self._sf() as db:
            stmt = delete(CheckpointRecord).where(
                CheckpointRecord.team_run_id == team_run_id
            )
            result = await db.execute(stmt)
            await db.commit()
            return result.rowcount

    @staticmethod
    def _safe_json(obj: Any) -> Any:
        """Return obj if JSON-serializable, else None."""
        if obj is None:
            return None
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Null fallback (no-PG / tests)
# ---------------------------------------------------------------------------


class NullCheckpointStore:
    """No-op store for when PostgreSQL is unavailable."""

    initialized: bool = False

    async def save(self, checkpoint: Any) -> None:
        pass

    async def load_latest(self, team_run_id: str) -> None:
        return None

    async def load_by_id(self, checkpoint_id: str, team_run_id: str) -> None:
        return None

    async def list_for_run(self, team_run_id: str) -> list:
        return []

    async def delete_for_run(self, team_run_id: str) -> int:
        return 0
