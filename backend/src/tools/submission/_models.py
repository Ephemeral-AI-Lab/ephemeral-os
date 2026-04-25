"""Shared pydantic models for the submission/accessor tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PhaseEntry(BaseModel):
    """A single entry inside one phase of a phased handoff plan."""

    id: str = Field(..., description="Task id; must be a key in task_specs.")
    needs: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of dep ids from strictly earlier phases. Omit for "
            "the implicit 'all of previous phase' default."
        ),
    )


class TaskSpec(BaseModel):
    """The descriptive part of a child task."""

    title: str = Field(..., min_length=1, description="Short title shown in UIs.")
    spec: str = Field(..., min_length=1, description="What the child must accomplish.")


class SubmissionOutput(BaseModel):
    """Generic output for the four submission tools."""

    status: str = Field(..., description="'accepted' on success, 'rejected' on validation failure.")
    detail: str | None = Field(default=None, description="Optional explanatory message.")


class TaskDetailsOutput(BaseModel):
    """Output for :func:`read_task_details`."""

    title: str
    spec: str
    status: str
    parent_id: str | None = None
    acceptance_criteria: str | None = None
    handoff_note: str | None = None
    summary: str | None = None


class TaskGraphChild(BaseModel):
    """A direct child task entry returned by :func:`read_task_graph`."""

    id: str
    title: str
    status: str
    summary: str | None = None


class TaskGraphOutput(BaseModel):
    """Output for :func:`read_task_graph` — direct children only (recursive opacity)."""

    children: list[TaskGraphChild]
