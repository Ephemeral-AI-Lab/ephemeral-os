"""Terminal tool: planner emits a full-plan DAG."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput, TaskDependencyEntry


class FullPlanInput(BaseModel):
    task_dep_graphs: list[TaskDependencyEntry] = Field(
        ...,
        description=(
            "Flat DAG plan: each entry is {id, deps, role}. List only DIRECT "
            "deps; transitive predecessors are implicit. Each role is a "
            "generator role: 'executor' or 'verifier'. The DAG must end in "
            "one final verifier that directly depends on every other node."
        ),
    )
    task_details: dict[str, str] = Field(
        ...,
        description=(
            "Map of task id -> task input string. Every entry id must be a key here."
        ),
    )

@tool(
    name="submit_full_plan",
    description=(
        "Terminal action (planner only) — emit a complete DAG plan. The "
        "harness materializes executor and verifier tasks with their direct "
        "dependencies. A 'full' plan must end in one verifier that depends on "
        "all other DAG nodes and closes the planning unit when it approves."
    ),
    input_model=FullPlanInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_full_plan(
    task_dep_graphs: list[dict],
    task_details: dict[str, str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "planner":
        return ToolResult(
            output=(
                "submit_full_plan is planner-only "
                f"(current role={role!r}); executors must use "
                "request_plan to spawn a planner instead."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_full_plan: missing task_center or task_id in metadata",
            is_error=True,
        )
    err = tc.submit_full_plan(task_id, task_dep_graphs, task_details)
    if err is not None:
        return ToolResult(
            output=f"plan rejected ({err.code}): {err.message}",
            is_error=True,
        )
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
