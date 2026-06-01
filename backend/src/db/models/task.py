"""Task persistence model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    from db.models.agent_run import AgentRunRecord
    from db.models.request import RequestRecord


class TaskRecord(Base):
    """Persisted task assigned to one agent run."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("requests.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32))
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    iteration_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    agent_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    needs: Mapped[list[str]] = mapped_column(JSON, default=list)
    outcomes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    terminal_tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    request: Mapped["RequestRecord"] = relationship(back_populates="tasks")
    agent_run: Mapped["AgentRunRecord | None"] = relationship(
        "AgentRunRecord",
        back_populates="task",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<TaskRecord id={self.id!r} status={self.status!r}>"
