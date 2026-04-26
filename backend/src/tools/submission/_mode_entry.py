"""Shared implementation for the mode-entry tools."""

from __future__ import annotations

from tools.core.base import ToolExecutionContext, ToolResult


def enter_secondary_mode(
    context: ToolExecutionContext,
    *,
    target_mode: str,
    required_role: str,
    briefing: str,
    tool_name: str,
) -> ToolResult:
    """Apply the four mode-entry guards, then flip ``Task.mode`` to *target_mode*.

    All guards return ``is_error=True`` ToolResults so the dispatcher's normal
    error-result flow surfaces them to the model without flipping the mode.

    On success — including the idempotent "already in target mode" case — the
    returned ToolResult carries the mode briefing as ``output`` and
    ``mode_transition=target_mode``. The dispatcher reads the latter to update
    ``QueryContext.active_mode`` after the turn.
    """
    if context.metadata.get("agent_type") == "subagent":
        return ToolResult(
            output=(
                f"{tool_name}: rejected — subagent contexts cannot toggle "
                "the parent task's mode. Subagents run their own task with "
                "their own mode field."
            ),
            is_error=True,
        )

    role = context.metadata.get("role")
    if role != required_role:
        return ToolResult(
            output=(
                f"{tool_name}: rejected — this tool is {required_role}-only "
                f"(current role={role!r})."
            ),
            is_error=True,
        )

    tc = context.metadata.get("task_center")
    task_id = context.metadata.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output=f"{tool_name}: missing task_center or task_id in metadata",
            is_error=True,
        )

    task = tc.graph.get(task_id)
    if task.mode == target_mode:
        # Idempotent: re-deliver the briefing without flipping anything.
        return ToolResult(output=briefing, mode_transition=target_mode)
    if task.mode != "direct":
        return ToolResult(
            output=(
                f"{tool_name}: rejected — task is already in mode "
                f"{task.mode!r}; cross-secondary transitions are not allowed. "
                f"Exit the current mode via its terminal tool first."
            ),
            is_error=True,
        )

    task.mode = target_mode
    return ToolResult(output=briefing, mode_transition=target_mode)
