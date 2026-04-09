"""Builders for system notifications emitted by the engine query loop.

Each helper returns ``None`` when no notification should fire, or a
``(history_message, stream_event)`` pair the loop appends to
``display_messages`` and yields to subscribers respectively. Keeping
this logic out of :mod:`engine.core.query` makes the loop body about
control flow only and gives notifications a single, easy-to-test home.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import math

from message.messages import ConversationMessage
from message.stream_events import SystemNotification

if TYPE_CHECKING:
    from engine.core.query import QueryContext


def get_planner_soft_limit(metadata: object | None) -> int:
    """Return the configured planner discovery budget, or ``0`` when unset."""
    if metadata is None:
        return 0
    try:
        return int(metadata.get("planner_soft_tool_limit") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def build_budget_warning(
    context: "QueryContext",
) -> tuple[ConversationMessage, SystemNotification] | None:
    """Warn the agent that its ``tool_call_limit`` is nearly exhausted.

    Fires when:
      - ``tool_call_limit`` is set, AND
      - remaining budget is ≤ 1 call OR ≤ 10% of the limit (whichever
        triggers first), AND
      - the budget is not yet fully exhausted (``remaining > 0``).

    Returns ``(message, event)``: the loop appends ``message`` to
    ``display_messages`` so the agent's next turn sees it, then yields
    ``event`` so subscribers (eval harness, UI) get a structured notice.
    """
    tool_metadata = context.tool_metadata
    soft_limit = context.planner_soft_tool_limit or get_planner_soft_limit(tool_metadata)
    if soft_limit > 0 and context.tool_calls_used >= soft_limit:
        warned_at = tool_metadata.get("_planner_soft_tool_limit_warned_at") if tool_metadata is not None else None
        if warned_at != soft_limit:
            if tool_metadata is not None:
                tool_metadata["_planner_soft_tool_limit_warned_at"] = soft_limit
            text = (
                f"[planning stop] You have already used {context.tool_calls_used} tool calls, which meets or "
                f"exceeds the SWE-EVO planner discovery budget ({soft_limit}). Reuse the evidence you already "
                "have, stop exploring, and emit the plan JSON now. Do not call more tools unless a submitted "
                "plan would be impossible without them."
            )
            return (
                ConversationMessage.from_user_text(text),
                SystemNotification(text=text, category="planning_stop"),
            )

    limit = context.tool_call_limit
    if limit is None:
        return None
    remaining = limit - context.tool_calls_used
    if remaining <= 0:
        return None  # exhausted — handled by loop termination
    threshold = max(3, math.ceil(limit * 0.25))
    should_warn = remaining in {threshold, 1}
    if not should_warn:
        return None
    if context.last_budget_warning_remaining == remaining:
        return None
    context.last_budget_warning_remaining = remaining
    text = (
        f"[budget warning] Only {remaining} of {limit} tool calls remain "
        f"({context.tool_calls_used} already used). "
        f"Stop exploring, reuse the evidence you already gathered, and submit "
        f"your final result now (submit_summary / submit_plan) before the "
        f"agent run is terminated."
    )
    return (
        ConversationMessage.from_user_text(text),
        SystemNotification(text=text, category="budget_warning"),
    )
