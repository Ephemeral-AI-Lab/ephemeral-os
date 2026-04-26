"""Tool-batch validation helpers for the query loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from message.messages import ToolResultBlock
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
    if not tool_calls or len(tool_calls) <= 1:
        return None

    # Terminal-tool and mode-entry-tool exclusivity: such tools mutate run
    # state in ways that would make sibling dispatches incoherent. If any
    # call in the batch is exclusive, the whole batch is rejected so the
    # model can resubmit with the exclusive tool alone.
    terminal_in_batch = [
        tc for tc in tool_calls if tc.name in context.terminal_tools
    ]
    registry = getattr(context, "tool_registry", None)
    entry_in_batch: list[Any] = []
    if registry is not None:
        for tc in tool_calls:
            tool_def = registry.get(tc.name)
            if tool_def is not None and getattr(tool_def, "is_mode_entry_tool", False):
                entry_in_batch.append(tc)

    flagged = terminal_in_batch + entry_in_batch
    if not flagged:
        return None

    flagged_names = ", ".join(sorted({f"`{tc.name}`" for tc in flagged}))
    called_names = ", ".join(f"`{tc.name}`" for tc in tool_calls)
    if terminal_in_batch and entry_in_batch:
        kind = "Terminal/mode-entry tool"
    elif entry_in_batch:
        kind = "Mode-entry tool"
    else:
        kind = "Terminal tool"
    message = (
        f"{kind} {flagged_names} must be called alone. "
        f"This response batched it with other tools: {called_names}. "
        f"No tool in this batch executed. "
        f"Resubmit with only the exclusive tool in its own final batch."
    )
    return reject_tool_batch(tool_calls, message=message)
