"""submit_execution_handoff delegated request tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from task_center import TaskCenterInvariantViolation
from tools._framework.core.context import ToolExecutionContextService
from sandbox._shared.models import Intent
from tools._framework.core.decorator import tool
from tools._framework.core.results import TextToolOutput, ToolResult
from tools.submission.context import (
    AttemptSubmissionContextError,
    resolve_executor_submission_context,
)
from .prompt import (
    get_submit_execution_handoff_description,
)

if TYPE_CHECKING:
    from task_center import StartedGoal


class RequestGoalSolutionInput(BaseModel):
    goal: str = Field(..., min_length=1)

    @field_validator("goal")
    @classmethod
    def _validate_goal(cls, value: str) -> str:
        if not value or value.isspace():
            raise ValueError("goal must be nonblank")
        return value


@tool(
    name="submit_execution_handoff",
    description=get_submit_execution_handoff_description(),
    input_model=RequestGoalSolutionInput,
    output_model=TextToolOutput,
    intent=Intent.READ_ONLY,
    is_terminal_tool=True,
)
async def submit_execution_handoff(
    goal: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_executor_submission_context(context)
    except AttemptSubmissionContextError as exc:
        return ToolResult(output=str(exc), is_error=True)

    try:
        started_goal: StartedGoal = (
            submission_context.start_delegated_goal(goal=goal)
        )
    except TaskCenterInvariantViolation as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output=(
            "Started delegated goal "
            f"{started_goal.goal_id} "
            "for this generator task."
        ),
        metadata={
            "submission_kind": "goal_start",
            "task_center_task_id": started_goal.origin.task_id,
            "attempt_id": started_goal.parent_attempt_id,
            "goal_id": started_goal.goal_id,
            "initial_iteration_id": started_goal.initial_iteration_id,
            "initial_attempt_id": started_goal.initial_attempt_id,
        },
    )
