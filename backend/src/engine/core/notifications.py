"""Builders for system notifications emitted by the engine query loop.

Each helper returns ``None`` when no notification should fire, or a
``(history_message, stream_event)`` pair the loop appends to
``display_messages`` and yields to subscribers respectively. Keeping
this logic out of :mod:`engine.core.query` makes the loop body about
control flow only and gives notifications a single, easy-to-test home.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from message.messages import ConversationMessage
from message.stream_events import SystemNotification

if TYPE_CHECKING:
    from engine.core.query import QueryContext


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
    limit = context.tool_call_limit
    if limit is None:
        return None
    remaining = limit - context.tool_calls_used
    if remaining <= 0:
        return None  # exhausted — handled by loop termination
    threshold = max(1, limit // 10)
    if remaining > 1 and remaining > threshold:
        return None
    if context.last_budget_warning_remaining == remaining:
        return None
    context.last_budget_warning_remaining = remaining
    text = (
        f"[budget warning] Only {remaining} of {limit} tool calls remaining. "
        f"Submit your final result now (submit_summary / submit_plan) or the "
        f"agent run will terminate when the budget is exhausted."
    )
    return (
        ConversationMessage.from_user_text(text),
        SystemNotification(text=text, category="budget_warning"),
    )
