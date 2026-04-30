"""Soft reminder for disabled generator complex-task request starts after edit."""

from __future__ import annotations

from typing import Any

from message.messages import ConversationMessage, ToolUseBlock
from notification.rules import NotificationRule


_EDIT_TOOL_NAMES = frozenset(
    {
        "write_file",
        "edit_file",
        "delete_file",
        "move_file",
        "shell",
    }
)


def _generator_has_edited(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, ConversationMessage):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock) and block.name in _EDIT_TOOL_NAMES:
                return True
    return False


def make_request_after_edit_reminder() -> NotificationRule:
    def _trigger(messages: list[Any], context: Any) -> bool:
        del context
        return _generator_has_edited(messages)

    def _body(messages: list[Any], context: Any) -> str:
        del messages, context
        return (
            "request_complex_task_solution is disabled after the first edit. "
            "Finish through this generator agent's success or failure terminal."
        )

    return NotificationRule(
        name="request_complex_task_after_edit",
        trigger=_trigger,
        body=_body,
    )
