"""Tool-result history and post-dispatch effects for query turns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.core.turn_request import (
    QueryTurnRequest,
    record_hook_system_reminder,
    record_tool_result_message,
)
from message.messages import ConversationMessage, ToolResultBlock
from notification.reminders import system_reminders_from_metadata

if TYPE_CHECKING:
    from engine.core.query import QueryContext


def append_tool_result_history(
    messages: list[ConversationMessage],
    tool_results: list[ToolResultBlock],
    *,
    turn: QueryTurnRequest,
) -> None:
    tool_result_message = ConversationMessage(role="user", content=tool_results)
    messages.append(tool_result_message)
    record_tool_result_message(turn, tool_result_message)

    system_reminders = []
    for result in tool_results:
        system_reminders.extend(system_reminders_from_metadata(dict(result.metadata or {})))
    if system_reminders:
        reminder_message = ConversationMessage(role="user", content=system_reminders)
        messages.append(reminder_message)
        record_hook_system_reminder(turn, reminder_message)


def apply_mode_transitions(
    context: QueryContext,
    tool_results: list[ToolResultBlock],
) -> None:
    # Entry tools are batch-exclusive (validate_tool_batch enforces it),
    # so at most one transition fires per turn. The loop remains defensive.
    if context.agent_def is None:
        return
    for result in tool_results:
        if result.mode_transition:
            next_mode = context.agent_def.modes_by_name.get(result.mode_transition)
            if next_mode is not None:
                context.active_mode = next_mode


def any_terminal_result(tool_results: list[ToolResultBlock]) -> bool:
    """True when a successful terminal tool result ended the query."""
    return any(result.does_terminate for result in tool_results)
