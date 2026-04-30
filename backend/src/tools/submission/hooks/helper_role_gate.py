"""Role metadata gate for helper and subagent terminal tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult


@dataclass(frozen=True, slots=True)
class HelperRoleGate:
    target_tool: str
    expected_role: str
    expected_agent_type: str | None = None

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        role = str(context.get("role") or "")
        if role and role != self.expected_role:
            return HookResult.fail(
                f"{self.target_tool} is only valid for {self.expected_role} runs."
            )
        agent_type = str(context.get("agent_type") or "")
        if (
            self.expected_agent_type is not None
            and agent_type
            and agent_type != self.expected_agent_type
        ):
            return HookResult.fail(
                f"{self.target_tool} is only valid for "
                f"{self.expected_agent_type} runs."
            )
        return HookResult.pass_(tool_input)
