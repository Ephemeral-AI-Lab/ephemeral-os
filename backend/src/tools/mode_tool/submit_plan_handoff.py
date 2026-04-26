"""Terminal tool: planner emits a DAG plan."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center.planning import PlanValidationError
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput, TaskDependencyEntry


class PlanHandoffInput(BaseModel):
    tasks: list[TaskDependencyEntry] = Field(
        ...,
        description=(
            "Flat DAG plan: each entry is {id, deps}. List only DIRECT deps; "
            "transitive predecessors are implicit."
        ),
    )
    task_inputs: dict[str, str] = Field(
        ...,
        description=(
            "Map of task id -> task input string. Every entry id must be a key here."
        ),
    )
    handoff_summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Articulation of what the plan covers and what the evaluator should "
            "verify before declaring success."
        ),
    )


@tool(
    name="submit_plan_handoff",
    description=(
        "Terminal (planner-only): emit the DAG plan. TaskCenter materializes "
        "executor children with their direct dependencies and an evaluator "
        "inside the planner's harness graph."
    ),
    input_model=PlanHandoffInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_plan_handoff(
    tasks: list[dict],
    task_inputs: dict[str, str],
    handoff_summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "planner":
        return ToolResult(
            output=(
                "submit_plan_handoff is planner-only "
                f"(current role={role!r}); executors and evaluators must use "
                "launch_plan_handoff to spawn a planner instead."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_plan_handoff: missing task_center or task_id in metadata",
            is_error=True,
        )
    try:
        tc.submit_plan_handoff(task_id, tasks, task_inputs, handoff_summary)
    except PlanValidationError as exc:
        return ToolResult(output=f"plan rejected: {exc}", is_error=True)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
