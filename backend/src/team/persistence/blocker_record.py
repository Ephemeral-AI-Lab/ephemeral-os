"""SQLAlchemy ORM model for the ``blockers`` table.

Stores blocker state durably so it survives conductor crash/restart.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BlockerRecord(Base):
    """Durable record of a blocker in the conductor."""

    __tablename__ = "blockers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="assessing")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_paths: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    initiating_task_id: Mapped[str] = mapped_column(Text, nullable=False)
    fix_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    declared_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    fix_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_assessments: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    resolved_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<BlockerRecord id={self.id!r} status={self.status!r} "
            f"reason={self.reason!r}>"
        )
