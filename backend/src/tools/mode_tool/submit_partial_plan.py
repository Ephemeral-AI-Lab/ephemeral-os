"""Terminal tool: planner emits a partial-plan DAG (Stage 3 of the four-role roadmap)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput, TaskDependencyEntry


class PartialPlanInput(BaseModel):
    task_dep_graphs: list[TaskDependencyEntry] = Field(
        ...,
        description=(
            "Flat DAG plan covering the next bounded segment: each entry is "
            "{id, deps, role}. Verifiers cannot be DAG sinks."
        ),
    )
    task_details: dict[str, str] = Field(
        ...,
        description=(
            "Map of task id -> task input string. Every entry id must be a key here."
        ),
    )
    what_to_do_next: str = Field(
        ...,
        min_length=1,
        description=(
            "Directive form of REPLAN_AFTER: instructions for the *next* "
            "planner that will continue from this segment's evaluator success. "
            "Stored on the harness graph for the Stage 5 continuation chain."
        ),
    )
    evaluation_specification: str = Field(
        ...,
        min_length=1,
        description=(
            "Explicit instruction to the auto-spawned evaluator gating this "
            "segment's checkpoint: what to verify, what to skip, which "
            "adversarial probes are most relevant."
        ),
    )


@tool(
    name="submit_partial_plan",
    description=(
        "Terminal action (planner only) — emit a partial DAG plan whose "
        "evaluator success triggers a continuation graph. Use when the next "
        "segment is bounded and verifiable but the tail's planning depends "
        "on what this segment uncovers (e.g., canary-then-fan-out, "
        "shim-then-bulk-migration)."
    ),
    input_model=PartialPlanInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_partial_plan(
    task_dep_graphs: list[dict],
    task_details: dict[str, str],
    what_to_do_next: str,
    evaluation_specification: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "planner":
        return ToolResult(
            output=(
                "submit_partial_plan is planner-only "
                f"(current role={role!r}); executors and evaluators must use "
                "request_plan to spawn a planner instead."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_partial_plan: missing task_center or task_id in metadata",
            is_error=True,
        )
    err = tc.submit_partial_plan(
        task_id,
        task_dep_graphs,
        task_details,
        what_to_do_next,
        evaluation_specification,
    )
    if err is not None:
        return ToolResult(
            output=f"plan rejected ({err.code}): {err.message}",
            is_error=True,
        )
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
