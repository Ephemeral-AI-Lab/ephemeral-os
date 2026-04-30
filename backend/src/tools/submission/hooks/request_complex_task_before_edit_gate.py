"""Prehook blocking complex-task handoff after the first edit-capable call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from message.messages import ConversationMessage, ToolUseBlock
from pydantic import BaseModel

from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult


EDIT_TOOL_NAMES = frozenset(
    {
        "write_file",
        "edit_file",
        "delete_file",
        "move_file",
        "shell",
    }
)


def executor_has_edited(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, ConversationMessage):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock) and block.name in EDIT_TOOL_NAMES:
                return True
    return False


@dataclass(frozen=True, slots=True)
class RequestComplexTaskBeforeEditGate:
    target_tool: str = "request_complex_task_solution"

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        messages = context.get("conversation_messages", [])
        if isinstance(messages, list) and executor_has_edited(messages):
            return HookResult.fail(
                "request_complex_task_solution is disabled after the first edit. "
                "Finish with submit_execution_success or submit_execution_failure."
            )
        return HookResult.pass_(tool_input)
