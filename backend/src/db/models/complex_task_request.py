"""ComplexTaskRequest persistence model — origin axis of harness work.

A ComplexTaskRequest is created when a generator task calls
``request_complex_task_solution(goal)``. It owns an ordered list of
``TaskSegment`` ids representing the vertical (continuation) progression of
work toward the request's goal.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class ComplexTaskRequestRecord(Base):
    """Persisted ComplexTaskRequest (origin axis)."""

    __tablename__ = "complex_task_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_center_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("task_center_runs.id", ondelete="CASCADE"),
        index=True,
    )
    requested_by_task_id: Mapped[str] = mapped_column(String(96), index=True)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    task_segment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    final_outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
            f"<ComplexTaskRequestRecord id={self.id!r} status={self.status!r}>"
        )
