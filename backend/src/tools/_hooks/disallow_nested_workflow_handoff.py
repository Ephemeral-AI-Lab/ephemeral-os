"""Prehook that blocks workflow handoff from nested generator tasks."""

from __future__ import annotations

from pydantic import BaseModel

from task_center._core.workflow_depth import is_nested_workflow
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.hooks import HookResult
from tools.submission.context import (
    AttemptSubmissionContextError,
    resolve_generator_submission_context,
)


_MSG_BLOCKED = (
    "BLOCKED: nested workflow generators cannot call submit_workflow_handoff. "
    "Finish this generator task with submit_generator_outcome(status=\"success\", ...) "
    "or submit_generator_outcome(status=\"failed\", ...)."
)


class DisallowNestedWorkflowHandoff:
    """Reject ``submit_workflow_handoff`` below the root workflow."""

    def __init__(self, target_tool: str) -> None:
        self.target_tool = target_tool
        self.name = f"no_nested_workflow_handoff:{target_tool}"

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[BaseModel]:
        try:
            submission_context = resolve_generator_submission_context(context)
        except AttemptSubmissionContextError as exc:
            return HookResult.fail(str(exc), metadata={"policy": "nested_workflow_handoff"})
        if not is_nested_workflow(
            workflow_id=submission_context.attempt_ctx.workflow.id,
            deps=submission_context.runtime,
        ):
            return HookResult.pass_(tool_input)
        return HookResult.fail(
            _MSG_BLOCKED,
            metadata={"policy": "nested_workflow_handoff", "reason": "nested_workflow"},
        )


__all__ = ["DisallowNestedWorkflowHandoff"]
