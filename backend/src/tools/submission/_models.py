"""Shared pydantic models for the submission tools."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field


class TaskDependencyEntry(BaseModel):
    """A single entry in a flat DAG plan: a task id and its direct deps."""

    id: str = Field(..., description="Task id; must be a key in task_specs.")
    deps: list[str] = Field(
        default_factory=list,
        description=(
            "Direct dependency ids. Transitive deps are implicit via the graph "
            "— do not list indirect predecessors."
        ),
    )


class TaskSpec(BaseModel):
    """The descriptive part of a child task."""

    title: str = Field(..., min_length=1, description="Short title shown in UIs.")
    task_input: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("task_input", "spec"),
        description="Input prompt for the child task. The legacy 'spec' key is accepted.",
    )


class SubmissionOutput(BaseModel):
    """Generic output for the four submission tools."""

    status: str = Field(..., description="'accepted' on success, 'rejected' on validation failure.")
    detail: str | None = Field(default=None, description="Optional explanatory message.")
