"""Role and ownership gate for harness attempt terminal tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from task_center.api import AttemptRuntime, HarnessTaskRole
from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult


@dataclass(frozen=True, slots=True)
class HarnessRoleGate:
    target_tool: str
    expected_role: HarnessTaskRole

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        runtime = context.get("attempt_runtime")
        if not isinstance(runtime, AttemptRuntime):
            return HookResult.fail(
                "Missing harness attempt runtime for this TaskCenter submission."
            )
        task_id = str(context.get("task_center_task_id") or "")
        if not task_id or task_id.isspace():
            return HookResult.fail(
                "Missing TaskCenter task id for this submission."
            )
        task = runtime.task_store.get_task(task_id)
        if task is None:
            return HookResult.fail(f"TaskCenter task {task_id!r} was not found.")

        actual_role = str(task.get("role") or "")
        if actual_role != self.expected_role.value:
            return HookResult.fail(
                f"{self.target_tool} is only valid for "
                f"{self.expected_role.value} tasks."
            )

        # Generator-role tasks may be the top-level entry executor; the
        # closed-attempt check only applies when there's a attempt.
        attempt_id = str(task.get("task_center_attempt_id") or "")
        if self.expected_role != HarnessTaskRole.GENERATOR and not attempt_id:
            return HookResult.fail(
                f"TaskCenter task {task_id!r} is not attached to a harness attempt."
            )
        if attempt_id:
            attempt = runtime.attempt_store.get(attempt_id)
            if attempt is None:
                return HookResult.fail(
                    f"Attempt {attempt_id!r} was not found."
                )
            if attempt.is_closed:
                return HookResult.fail(
                    "This harness attempt is already closed; terminal submissions are disabled."
                )
        return HookResult.pass_(tool_input)
