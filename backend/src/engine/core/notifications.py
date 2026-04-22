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
            "1. Reserve one call for submit_task_summary; never spend the last tool call on CodeAct, reads, diagnostics, or cleanup.\n"
            "2. Run one final exact verification command (daytona_codeact) only if you can still reserve the terminal summary call.\n"
            "3. Call submit_task_summary(type='success') for PASS with exact commands, exit codes, and a Residual Risk line, or submit_task_summary(type='request_replan') with exact evidence for FAILURE."
        )
    return (
        "1. Reserve one call for submit_task_summary; never spend the last tool call on CodeAct, reads, diagnostics, or cleanup.\n"
        "2. Run at most one final verification or diagnostics pass only if you can still reserve the terminal summary call.\n"
        "3. If evidence is incomplete, diagnostics-only, verification was not run due to budget, verification still fails, or diagnostics cannot be finished within budget, call submit_task_summary(type='request_replan') with the exact evidence now.\n"
        "4. If the latest required verification passed after the final edit and diagnostics are clean, call submit_task_summary(type='success') with behavior/API delta, exact commands and exit codes, diagnostics status, and a Residual Risk line."
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
        f"Stop editing and exploring immediately. Terminal submission counts against this budget; "
        f"keep one call reserved for the role-correct terminal tool. Your next actions must be:\n"
        f"{_budget_warning_steps(context)}\n"
        f"Do NOT start new edits, file reads, or debugging loops. Submit now."
    )
    return (
        ConversationMessage.from_user_text(text),
        SystemNotification(text=text, category="budget_warning"),
    )
