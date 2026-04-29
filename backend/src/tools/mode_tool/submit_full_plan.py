"""Terminal tool: planner emits a full-plan DAG (Stage 3 of the four-role roadmap)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center.runtime.pre_hooks import BlockedTerminal, check_advisor_accept
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput, TaskDependencyEntry


class FullPlanInput(BaseModel):
    task_dep_graphs: list[TaskDependencyEntry] = Field(
        ...,
        description=(
            "Flat DAG plan: each entry is {id, deps, role}. List only DIRECT "
            "deps; transitive predecessors are implicit. Each role is a "
            "generator role: 'executor' or 'verifier'."
        ),
    )
    task_details: dict[str, str] = Field(
        ...,
        description=(
            "Map of task id -> task input string. Every entry id must be a key here."
        ),
    )
    evaluation_specification: str = Field(
        ...,
        min_length=1,
        description=(
            "Explicit instruction to the auto-spawned evaluator: what to "
            "verify, what to skip, which adversarial probes are most relevant. "
            "Becomes the evaluator's task input."
        ),
    )


@tool(
    name="submit_full_plan",
    description=(
        "Terminal action (planner only) — emit a complete DAG plan. The "
        "harness materializes generator children (executors and verifiers) "
        "with their direct dependencies and a single evaluator at the DAG's "
        "sinks. A 'full' plan asserts the planning unit's goal can be met "
        "without further planner involvement once the evaluator approves."
    ),
    input_model=FullPlanInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_full_plan(
    task_dep_graphs: list[dict],
    task_details: dict[str, str],
    evaluation_specification: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "planner":
        return ToolResult(
            output=(
                "submit_full_plan is planner-only "
                f"(current role={role!r}); executors and evaluators must use "
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
    # Stage 4 first-cut gate: require a fresh advisor accept whose payload
    # exactly matches the call. Phase-1 lenient consumption — a
    # MaterializationFailure preserves the accept; only a successful
    # terminal or a divergent next ask_advisor invalidates it.
    proposed_input = {
        "task_dep_graphs": list(task_dep_graphs),
        "task_details": dict(task_details),
        "evaluation_specification": evaluation_specification,
    }
    try:
        check_advisor_accept(
            tc, task_id, "submit_full_plan", proposed_input
        )
    except BlockedTerminal as block:
        return ToolResult(output=str(block), is_error=True)
    err = tc.submit_full_plan(
        task_id, task_dep_graphs, task_details, evaluation_specification
    )
    if err is not None:
        # Lenient Phase 1: failure is a tool-result failure (not a terminal),
        # so the planner can correct and retry without re-consulting the advisor.
        return ToolResult(
            output=f"plan rejected ({err.code}): {err.message}",
            is_error=True,
        )
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
