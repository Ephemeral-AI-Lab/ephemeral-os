"""SQLAlchemy models for pipeline persistence."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class PipelineDefinitionRecord(Base):
    """A saved pipeline configuration."""

    __tablename__ = "pipeline_definitions"

    pipeline_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<PipelineDefinition id={self.pipeline_id!r} name={self.name!r}>"


class PipelineRunRecord(Base):
    """A pipeline execution run."""

    __tablename__ = "pipeline_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(String(128), index=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_steps: Mapped[list] = mapped_column(JSON, default=list)
    context_map: Mapped[dict] = mapped_column(JSON, default=dict)
    step_records: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    resumed_from_checkpoint: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<PipelineRun id={self.run_id!r} status={self.status!r}>"


class PipelineCheckpointRecord(Base):
    """A checkpoint snapshot at a step boundary."""

    __tablename__ = "pipeline_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    step_name: Mapped[str] = mapped_column(String(128))
    step_index: Mapped[int] = mapped_column(Integer)
    context_map_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    completed_steps: Mapped[list] = mapped_column(JSON, default=list)
    step_records: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)

    def __repr__(self) -> str:
        return f"<PipelineCheckpoint id={self.checkpoint_id!r} step={self.step_name!r}>"
