"""Tool-batch validation helpers for the query loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from message.messages import ToolResultBlock
from tools.builtins.skills.toolkit import (
    get_reference_terminal_action,
    get_required_next_tool,
)

if TYPE_CHECKING:
    from engine.core.query import QueryContext


def reject_tool_batch(
    tool_calls: list[Any],
    message: str,
) -> list[ToolResultBlock]:
    return [
        ToolResultBlock(tool_use_id=str(tc.id), content=message, is_error=True) for tc in tool_calls
    ]


def validate_tool_batch(
    context: QueryContext,
    tool_calls: list[Any],
) -> list[ToolResultBlock] | None:
    if not tool_calls:
        return None

    pending = get_required_next_tool(context.tool_metadata)
    if pending is not None:
        if len(tool_calls) != 1 or tool_calls[0].name != pending["tool_name"]:
            called = ", ".join(f"`{tc.name}`" for tc in tool_calls)
            message = (
                f"{pending.get('reason') or 'A terminal tool-call guard is active.'} "
                f"The next tool must be `{pending['tool_name']}(...)`. "
                f"This response tried to call {called}. "
                f"Submit only `{pending['tool_name']}(...)` in the next tool batch. "
                f"{pending.get('reset_hint') or ''}"
            ).strip()
            return reject_tool_batch(tool_calls, message=message)
        return None

    terminal_reference = None
    for tc in tool_calls:
        terminal_reference = get_reference_terminal_action(tc.name, tc.input)
        if terminal_reference is not None:
            break
    if terminal_reference is None:
        return None
    if len(tool_calls) == 1:
        return None

    called = ", ".join(f"`{tc.name}`" for tc in tool_calls)
    message = (
        f"{terminal_reference.get('reason') or 'A terminal reference is active.'} "
        f"`{terminal_reference['skill_name']}/{terminal_reference['reference_name']}` "
        "must be loaded alone so the next tool batch can end with the required "
        f"`{terminal_reference['tool_name']}(...)` action. "
        f"This response tried to call {called}. "
        "Restart the ending chain sequentially instead of batching the final references. "
        f"{terminal_reference.get('reset_hint') or ''}"
    ).strip()
    return reject_tool_batch(tool_calls, message=message)
