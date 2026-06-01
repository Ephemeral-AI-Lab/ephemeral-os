"""Workflow persistence model."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class WorkflowRecord(Base):
    """Persisted Workflow (origin axis)."""

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("requests.id", ondelete="CASCADE"),
        index=True,
    )
    parent_task_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    iteration_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    outcomes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
