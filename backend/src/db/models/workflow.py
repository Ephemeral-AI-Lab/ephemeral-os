"""Workflow persistence model — origin axis of harness work.

Every Workflow is generator-spawned (the root via a synthetic run-level
bootstrap generator task) through ``submit_workflow_handoff(goal)``;
``parent_task_id`` is the backward link to the spawning task. It owns an
ordered list of ``Iteration`` ids representing the vertical continuation
progression of work toward the goal.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class WorkflowRecord(Base):
    """Persisted Workflow (origin axis)."""

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_center_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("task_center_runs.id", ondelete="CASCADE"),
        index=True,
    )
    parent_task_id: Mapped[str | None] = mapped_column(
        String(96), nullable=True, index=True
    )
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    iteration_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
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
    def __repr__(self) -> str:
        return (
            f"<WorkflowRecord id={self.id!r} status={self.status!r}>"
        )
