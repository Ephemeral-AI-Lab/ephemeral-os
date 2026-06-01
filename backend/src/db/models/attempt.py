"""Attempt persistence model — horizontal-retry axis of harness work.

An Attempt is one planner-authored plan (a DAG of generator + reducer tasks)
run inside an Iteration.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class AttemptRecord(Base):
    """Persisted Attempt (horizontal retry axis)."""

    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    iteration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("iterations.id", ondelete="CASCADE"),
        index=True,
    )
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_sequence_no: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    planner_task_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    generator_task_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    reducer_task_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    outcomes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    deferred_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        UniqueConstraint(
            "iteration_id",
            "attempt_sequence_no",
            name="uq_attempt_iteration_sequence",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AttemptRecord id={self.id!r} "
            f"seq={self.attempt_sequence_no} stage={self.stage!r}>"
        )
