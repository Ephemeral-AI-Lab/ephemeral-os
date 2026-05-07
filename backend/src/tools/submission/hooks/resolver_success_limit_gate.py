"""Prehook blocking success terminals after unresolved resolver calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult
from tools.submission.resolver_history import unresolved_resolver_call_count


@dataclass(frozen=True, slots=True)
class ResolverSuccessLimitGate:
    target_tool: str
    limit: int = 5

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        messages = context.get("conversation_messages", [])
        count = unresolved_resolver_call_count(messages if isinstance(messages, list) else [])
        if count >= self.limit:
            return HookResult.fail(
                "Success is blocked after five unresolved resolver calls. Submit "
                "the corresponding failure terminal with the remaining issues."
            )
        return HookResult.pass_(tool_input)
