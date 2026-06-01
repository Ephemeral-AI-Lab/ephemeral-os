"""Request persistence model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    from db.models.task import TaskRecord


class RequestRecord(Base):
    """One top-level user request and its result status."""

    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cwd: Mapped[str] = mapped_column(String(1024))
    sandbox_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_prompt: Mapped[str] = mapped_column(Text)
    root_task_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tasks: Mapped[list["TaskRecord"]] = relationship(
        "TaskRecord",
        back_populates="request",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<RequestRecord id={self.id!r} status={self.status!r}>"
