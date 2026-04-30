"""Role and ownership gate for harness graph terminal tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from task_center.task import HarnessTaskRole
from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)


@dataclass(frozen=True, slots=True)
class HarnessRoleGate:
    target_tool: str
    expected_role: HarnessTaskRole

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        try:
            submission_context = resolve_harness_submission_context(context)
        except HarnessSubmissionContextError as exc:
            return HookResult.fail(str(exc))

        actual_role = str(submission_context.task.get("role") or "")
        if actual_role != self.expected_role.value:
            return HookResult.fail(
                f"{self.target_tool} is only valid for "
                f"{self.expected_role.value} tasks."
            )
        if submission_context.graph.is_closed:
            return HookResult.fail(
                "This harness graph is already closed; terminal submissions are disabled."
            )
        return HookResult.pass_(tool_input)
