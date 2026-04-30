"""Profile-role gate for executor- vs verifier-only generator terminals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult


@dataclass(frozen=True, slots=True)
class HarnessAgentProfileGate:
    """Reject the call when the running agent's profile role does not match.

    The structural ``HarnessRoleGate`` only proves the persisted task row is a
    generator. This gate asserts the spawned agent's profile role (``executor``
    or ``verifier``) matches the tool's contract, so a verifier-launched
    generator task cannot reach an executor terminal.
    """

    target_tool: str
    expected_profile_role: str

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        actual_role = str(context.get("role") or "")
        if actual_role != self.expected_profile_role:
            return HookResult.fail(
                f"{self.target_tool} requires the {self.expected_profile_role} "
                "agent profile."
            )
        return HookResult.pass_(tool_input)
