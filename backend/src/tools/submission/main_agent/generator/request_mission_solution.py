"""request_mission_solution delegated request tool."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from task_center.api import (
    HarnessTaskRole,
    StartedMission,
    TaskCenterInvariantViolation,
)
from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.context import (
    AttemptSubmissionContextError,
    resolve_executor_submission_context,
)
from tools.submission.hooks import (
    HarnessAgentProfileGate,
    HarnessRoleGate,
    RequestMissionBeforeEditGate,
)


class RequestMissionSolutionInput(BaseModel):
    goal: str = Field(..., min_length=1)

    @field_validator("goal")
    @classmethod
    def _validate_goal(cls, value: str) -> str:
        if not value or value.isspace():
            raise ValueError("goal must be nonblank")
        return value


@tool(
    name="request_mission_solution",
    description=(
        "Request a delegated complex-task solution for the current generator task. "
        "This must be called before making edits."
    ),
    input_model=RequestMissionSolutionInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HarnessRoleGate("request_mission_solution", HarnessTaskRole.GENERATOR),
        HarnessAgentProfileGate(
            target_tool="request_mission_solution",
            expected_profile_role="executor",
        ),
        RequestMissionBeforeEditGate(),
    ),
)
async def request_mission_solution(
    goal: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_executor_submission_context(context)
    except AttemptSubmissionContextError as exc:
        return ToolResult(output=str(exc), is_error=True)

    try:
        started_request: StartedMission = (
            submission_context.start_mission_request(goal=goal)
        )
    except TaskCenterInvariantViolation as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output=(
            "Started delegated mission request "
            f"{started_request.mission_id} "
            "for this generator task."
        ),
        metadata={
            "submission_kind": "mission_start",
            "task_center_task_id": started_request.parent_task_id,
            "attempt_id": started_request.parent_attempt_id,
            "mission_id": started_request.mission_id,
            "initial_episode_id": started_request.initial_episode_id,
            "initial_attempt_id": started_request.initial_attempt_id,
        },
    )
