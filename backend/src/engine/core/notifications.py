"""Builders for system notifications emitted by the engine query loop.

Each helper returns ``None`` when no notification should fire, or a
``(history_message, stream_event)`` pair the loop appends to
``display_messages`` and yields to subscribers respectively. Keeping
this logic out of :mod:`engine.core.query` makes the loop body about
control flow only and gives notifications a single, easy-to-test home.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from message.messages import ConversationMessage
from message.stream_events import SystemNotification

if TYPE_CHECKING:
    from engine.core.query import QueryContext


def _budget_warning_steps(context: "QueryContext") -> str:
    role = ""
    if context.tool_metadata is not None:
        role = str(context.tool_metadata.get("role", "") or "")

    if role == "planner":
        return (
            "1. Stop exploring and shaping new lanes immediately.\n"
            "2. Call submit_plan() with the strongest plan you can defend right now. If the budget is nearly gone, submit_plan() is your next call."
        )
    if role == "replanner":
        return (
            "1. Stop reopening ownership questions immediately.\n"
            "2. Call submit_replan() with the corrective action you can already justify. If the budget is nearly gone, submit_replan() is your next call."
        )
    if role == "reviewer":
        return (
            "1. Reserve one call for submit_task_success or request_replan; never spend the last tool call on daytona_shell, reads, diagnostics, or cleanup.\n"
            "2. Run one final exact verification command (daytona_shell) only if you can still reserve the terminal submission call.\n"
            "3. Call submit_task_success() for PASS with exact commands, exit codes, and diagnostics status, or request_replan() with exact evidence for FAILURE."
        )
    remaining = context.tool_call_limit - context.tool_calls_used if context.tool_call_limit else 0
    if remaining <= 1:
        return (
            "1. Use this last call for submit_task_success or request_replan.\n"
            "2. If evidence is incomplete, diagnostics-only, red, absent, or invalid, call request_replan() with exact evidence.\n"
            "3. Call submit_task_success() only when latest required verification is green and diagnostics are clean."
        )
    return (
        "1. Reserve one call for submit_task_success or request_replan; never spend the last tool call on daytona_shell, reads, diagnostics, or cleanup.\n"
        "2. Continue only with a bounded known fix, required diagnostic, or exact verification that still leaves a terminal call.\n"
        "3. If evidence is incomplete when only the terminal call remains, call request_replan() with exact evidence.\n"
        "4. Call submit_task_success() only when latest required verification is green and diagnostics are clean."
    )


def build_budget_warning(
    context: "QueryContext",
) -> tuple[ConversationMessage, SystemNotification] | None:
    """Warn the agent that its ``tool_call_limit`` is nearly exhausted.

    Fires when:
      - ``tool_call_limit`` is set, AND
      - used budget has reached 75% of the limit OR only 1 call remains, AND
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
    used_threshold = max(1, math.ceil(limit * 0.75))
    should_warn = context.tool_calls_used in {used_threshold, limit - 1}
    if not should_warn:
        return None
    if context.last_budget_warning_remaining == remaining:
        return None
    context.last_budget_warning_remaining = remaining
    text = (
        f"[budget warning] Only {remaining} of {limit} tool calls remain "
        f"({context.tool_calls_used} already used). "
        f"This is an advisory warning, not a terminal trigger. Terminal submission counts against this budget; "
        f"keep one call reserved for the role-correct terminal tool. "
        f"Prepare the terminal path while using any remaining safe calls deliberately:\n"
        f"{_budget_warning_steps(context)}\n"
        f"Do not spend the final reserved call on non-terminal mutation or investigation. "
        f"Once only the terminal call remains, partial or red work belongs in request_replan()."
    )
    return (
        ConversationMessage.from_user_text(text),
        SystemNotification(text=text, category="budget_warning"),
    )
