"""Prehook that blocks planner deferral inside delegated workflows."""

from __future__ import annotations

from pydantic import BaseModel

from task_center._core.workflow_depth import is_nested_workflow
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.hooks import HookResult
from tools.submission.context import (
    AttemptSubmissionContextError,
    resolve_attempt_submission_context,
)


_MSG_BLOCKED = (
    "BLOCKED: nested workflow planners cannot set "
    "deferred_goal_for_next_iteration. Submit a plan that covers all "
    "current child-workflow goal items and leaves no remaining items."
)


class DisallowNestedPlannerDeferral:
    """Reject ``submit_planner_outcome`` deferrals below the root workflow."""

    def __init__(self, target_tool: str) -> None:
        self.target_tool = target_tool
        self.name = f"no_nested_planner_deferral:{target_tool}"

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[BaseModel]:
        deferred = getattr(tool_input, "deferred_goal_for_next_iteration", None)
        if not isinstance(deferred, str) or not deferred.strip():
            return HookResult.pass_(tool_input)
        try:
            submission_context = resolve_attempt_submission_context(context)
        except AttemptSubmissionContextError as exc:
            return HookResult.fail(str(exc), metadata={"policy": "nested_planner_deferral"})
        if not is_nested_workflow(
            workflow_id=submission_context.workflow.id,
            deps=submission_context.runtime,
        ):
            return HookResult.pass_(tool_input)
        return HookResult.fail(
            _MSG_BLOCKED,
            metadata={"policy": "nested_planner_deferral", "reason": "nested_workflow"},
        )


__all__ = ["DisallowNestedPlannerDeferral"]
