"""Agent run and response chunk models."""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class AgentRunRecord(Base):
    """A single agent execution within a session."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    input_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    compacted_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    session: Mapped[SessionRecord] = relationship(back_populates="runs")  # noqa: F821
    chunks: Mapped[list[AgentResponseChunkRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AgentRunRecord id={self.id!r} agent={self.agent_name!r} status={self.status!r}>"


class AgentResponseChunkRecord(Base):
    """An individual response fragment from an agent run."""

    __tablename__ = "agent_response_chunks"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    event_kind: Mapped[str] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    run: Mapped[AgentRunRecord] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<AgentResponseChunkRecord seq={self.seq} kind={self.event_kind!r}>"
