"""Budget-warning rule factory.

Replaces the imperative `notify_system` call previously made from
`tools/core/tool_execution.py`. Fires once per crossed budget threshold;
the rule's own state in `context.notification_state['budget_warning']`
enforces "once per threshold" semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from notification.rules import MessageList, NotificationRule

if TYPE_CHECKING:
    from engine.api import QueryContext


_DEFAULT_THRESHOLDS: tuple[float, ...] = (0.50, 0.75, 0.90)
_STATE_KEY = "budget_warning"


def make_budget_warning(
    thresholds: tuple[float, ...] = _DEFAULT_THRESHOLDS,
) -> NotificationRule:
    """Build a rule that emits a budget reminder at each crossed threshold.

    `thresholds` is a tuple of fractions (e.g., 0.5, 0.75, 0.9). The rule
    fires at most once per threshold per run; subsequent invocations at the
    same threshold are suppressed via per-rule state on `QueryContext`.
    """
    sorted_thresholds = tuple(sorted(thresholds))

    def _trigger(messages: MessageList, context: "QueryContext") -> bool:
        del messages
        budget = context.tool_budget
        if budget.limit is None or budget.limit <= 0:
            return False
        frac = budget.fraction_used
        state = context.notification_state.setdefault(
            _STATE_KEY, {"last_fired": 0.0, "pending_pct": None}
        )
        for t in sorted_thresholds:
            if frac >= t and state["last_fired"] < t:
                state["last_fired"] = t
                state["pending_pct"] = int(round(t * 100))
                return True
        return False

    def _body(messages: MessageList, context: "QueryContext") -> str:
        del messages
        pct = context.notification_state[_STATE_KEY]["pending_pct"]
        return (
            f"Tool-call budget at {pct}%. Prefer reads over writes, "
            f"batch where possible, and stop exploring once the goal is met."
        )

    return NotificationRule(
        name="budget_warning",
        body=_body,
        trigger=_trigger,
        fire_once=False,
    )
