"""Agent run model."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    from db.models.task_center import TaskCenterTaskRecord


class AgentRunRecord(Base):
    """One agent execution for one TaskCenter task."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("task_center_tasks.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    agent_name: Mapped[str] = mapped_column(String(128))
    message_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    terminal_tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["TaskCenterTaskRecord"] = relationship(
        "TaskCenterTaskRecord",
        back_populates="agent_run",
    )

    def __repr__(self) -> str:
        return f"<AgentRunRecord id={self.id!r} task_id={self.task_id!r}>"
