"""Pipeline runtime state models — execution tracking and checkpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class StepRecord:
    """Runtime record for a single step execution."""

    name: str
    agent: str
    status: str = StepStatus.PENDING
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    work_session_id: str | None = None
    posthook_session_id: str | None = None
    attempt: int = 1


@dataclass
class PipelineCheckpoint:
    """Snapshot at a step boundary.  Enables resume from any point."""

    checkpoint_id: str
    run_id: str
    step_name: str
    step_index: int
    context_map_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_steps: list[str] = field(default_factory=list)
    step_records: list[StepRecord] = field(default_factory=list)
    created_at: float = 0.0


@dataclass
class PipelineRun:
    """Full pipeline execution state with checkpoint-based retry."""

    run_id: str
    pipeline_id: str
    goal: str = ""
    status: str = PipelineRunStatus.PENDING
    current_step: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    context_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    step_records: list[StepRecord] = field(default_factory=list)
    checkpoints: list[PipelineCheckpoint] = field(default_factory=list)
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    # Retry state
    resumed_from_checkpoint: str | None = None
    attempt_number: int = 1
