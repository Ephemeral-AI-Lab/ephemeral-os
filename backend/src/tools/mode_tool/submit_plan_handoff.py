"""Terminal tool: planner emits a DAG plan."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center.graph import PlanValidationError
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
    handoff_plan_note: str = Field(
        ...,
        min_length=1,
        description=(
            "Articulation of the plan itself: PLAN_SHAPE, TOPOLOGY, "
            "COVERAGE_MAP, CONFIDENCE_BOUNDARY, GAP. Stored on the harness "
            "graph and surfaced to the evaluator."
        ),
    )
    evaluator_note: str = Field(
        ...,
        min_length=1,
        description=(
            "Explicit instruction to the evaluator that will gate this "
            "harness graph: what to verify, what to skip, which adversarial "
            "probes are most relevant. Becomes the evaluator's task input."
        ),
    )


@tool(
    name="submit_plan_handoff",
    description=(
        "Terminal action (planner only) — emit the executor DAG. TaskCenter materializes "
        "executor children with their direct dependencies and a single evaluator inside this "
        "harness graph. Each executor task description must be self-contained. The plan may be "
        "partial — note any uncertainty or future-step gaps in the handoff_plan_note."
    ),
    input_model=PlanHandoffInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_plan_handoff(
    tasks: list[dict],
    task_inputs: dict[str, str],
    handoff_plan_note: str,
    evaluator_note: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "planner":
        return ToolResult(
            output=(
                "submit_plan_handoff is planner-only "
                f"(current role={role!r}); executors and evaluators must use "
                "request_plan to spawn a planner instead."
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
        tc.submit_plan_handoff(
            task_id, tasks, task_inputs, handoff_plan_note, evaluator_note
        )
    except PlanValidationError as exc:
        return ToolResult(output=f"plan rejected: {exc}", is_error=True)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
