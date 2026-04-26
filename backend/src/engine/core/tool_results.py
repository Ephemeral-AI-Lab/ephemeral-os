"""Post-dispatch effects for assistant tool calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from message.messages import ToolResultBlock
from tools.core.base import ToolResult

if TYPE_CHECKING:
    from engine.core.query import QueryContext


def apply_mode_transitions(
    context: QueryContext,
    tool_results: list[ToolResultBlock],
) -> None:
    # Entry tools are batch-exclusive (validate_tool_batch enforces it), so at
    # most one transition fires for a response. The loop remains defensive.
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


def terminal_result_from_tool_results(
    tool_results: list[ToolResultBlock],
) -> ToolResult | None:
    for result in tool_results:
        if not result.does_terminate:
            continue
        return ToolResult(
            output=str(result.content),
            is_error=result.is_error,
            metadata=dict(result.metadata or {}),
            does_terminate=True,
            mode_transition=result.mode_transition,
        )
    return None
