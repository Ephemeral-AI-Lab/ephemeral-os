"""submit_full_plan terminal tool."""

from __future__ import annotations

from task_center.exceptions import GraphInvariantViolation
from task_center.task import HarnessTaskRole
from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)
from tools.submission.hooks import HarnessRoleGate
from tools.submission.main_agent.planner._schemas import (
    PlanTaskInput,
    PlannerSubmissionBaseInput,
    build_planner_submission,
)


class SubmitFullPlanInput(PlannerSubmissionBaseInput):
    pass


@tool(
    name="submit_full_plan",
    description="Submit a complete harness graph plan for the current segment.",
    input_model=SubmitFullPlanInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(HarnessRoleGate("submit_full_plan", HarnessTaskRole.PLANNER),),
)
async def submit_full_plan(
    task_specification: str,
    evaluation_criteria: list[str],
    tasks: list[PlanTaskInput],
    task_specs: dict[str, str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_harness_submission_context(context)
    except HarnessSubmissionContextError as exc:
        return ToolResult(output=str(exc), is_error=True)

    submission, error = build_planner_submission(
        submission_context=submission_context,
        kind="full",
        task_specification=task_specification,
        evaluation_criteria=evaluation_criteria,
        tasks=[PlanTaskInput.model_validate(task) for task in tasks],
        task_specs=task_specs,
        continuation_goal=None,
    )
    if error is not None or submission is None:
        return ToolResult(output=error or "Invalid planner submission.", is_error=True)

    try:
        submission_context.orchestrator.apply_plan_submission(submission)
    except GraphInvariantViolation as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted planner submission.",
        metadata={
            "submission_kind": "planner_full",
            "task_center_task_id": submission_context.task_center_task_id,
            "harness_graph_id": submission_context.graph.id,
        },
    )
