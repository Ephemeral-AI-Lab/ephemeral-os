"""Shared pydantic models for the mode tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaskDependencyEntry(BaseModel):
    """A single entry in a flat DAG plan: a task id and its direct deps."""

    id: str = Field(..., description="Task id; must be a key in task_inputs.")
    deps: list[str] = Field(
        default_factory=list,
        description=(
            "Direct dependency ids. Transitive deps are implicit via the graph "
            "— do not list indirect predecessors."
        ),
    )
    role: str = Field(
        default="executor",
        description=(
            "Generator role for this DAG node: 'executor' (default) for a "
            "DAG-local doer, or 'verifier' for a mid-graph node-scoped check. "
            "Verifiers cannot be DAG sinks."
        ),
    )


class SubmissionOutput(BaseModel):
    """Generic output for terminal tools."""

    status: str = Field(..., description="'accepted' on success, 'rejected' on validation failure.")
    detail: str | None = Field(default=None, description="Optional explanatory message.")
