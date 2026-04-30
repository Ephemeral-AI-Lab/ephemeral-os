"""Caller-role gate for blocking helper request tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult


@dataclass(frozen=True, slots=True)
class HelperRequestGate:
    target_tool: str
    allowed_caller_roles: frozenset[str]

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        role = str(context.get("role") or "").strip()
        if not role:
            return HookResult.fail(
                f"{self.target_tool} requires caller role metadata."
            )
        if role not in self.allowed_caller_roles:
            allowed = ", ".join(sorted(self.allowed_caller_roles))
            return HookResult.fail(
                f"{self.target_tool} can only be called by: {allowed}."
            )
        return HookResult.pass_(tool_input)
