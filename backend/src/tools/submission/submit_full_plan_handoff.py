"""Terminal tool: executor hands off a full phased plan."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from task_center.errors import PhaseValidationError
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool
from tools.submission._models import PhaseEntry, SubmissionOutput, TaskSpec


class FullPlanHandoffInput(BaseModel):
    phases: list[list[PhaseEntry]] = Field(
        ...,
        description="Ordered phases; each phase is a list of entries.",
    )
    task_specs: dict[str, TaskSpec] = Field(
        ...,
        description="Map of task id -> {title, spec}. Every phase entry id must be a key here.",
    )
    acceptance_criteria: str = Field(
        ...,
        min_length=1,
        description=(
            "Immutable success criteria for the handoff. The evaluator validates "
            "child outputs against this text."
        ),
    )


@tool(
    name="submit_full_plan_handoff",
    description=(
        "Terminal: hand off the full task as a phased plan. Use when the phases "
        "fully cover the acceptance_criteria. TaskCenter compiles the phases, "
        "spawns child executors, and runs one final evaluator after the final "
        "phase passes."
    ),
    input_model=FullPlanHandoffInput,
    output_model=SubmissionOutput,
)
async def submit_full_plan_handoff(
    phases: list[list[dict[str, Any]]],
    task_specs: dict[str, dict[str, Any]],
    acceptance_criteria: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    tc = context.metadata.get("task_center")
    task_id = context.metadata.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_full_plan_handoff: missing task_center or task_id in metadata",
            is_error=True,
        )
    try:
        tc.submit_full_handoff(task_id, phases, task_specs, acceptance_criteria)
    except PhaseValidationError as exc:
        return ToolResult(output=f"plan rejected: {exc}", is_error=True)
    return ToolResult(output="accepted")
