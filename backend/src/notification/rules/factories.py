"""Built-in notification rule factories.

Per-agent definitions assemble these factories into notification rule lists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from notification.rules.model import MessageList, NotificationRule

if TYPE_CHECKING:
    from engine.api import QueryContext


_DEFAULT_THRESHOLDS: tuple[float, ...] = (0.50, 0.75, 0.90)
_STATE_KEY = "budget_warning"
_OVERFLOW_STATE_KEY = "budget_overflow"


def make_opening_reminder(rules_text: str) -> NotificationRule:
    """Build a one-shot rule that emits `rules_text` on the first turn."""
    text = rules_text.strip()

    def _body(messages: MessageList, context: "QueryContext") -> str:
        del messages, context
        return text

    def _trigger(messages: MessageList, context: "QueryContext") -> bool:
        del context
        return not any(m.role == "assistant" for m in messages)

    return NotificationRule(
        name="opening_reminder",
        body=_body,
        trigger=_trigger,
        fire_once=True,
    )


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


def make_budget_overflow_reminder(every: int = 5) -> NotificationRule:
    """Emit a terminal-call nudge once `tool_overshoot` is positive, then
    again whenever overshoot has advanced by `every` since the last emission.

    Monotonic-crossing-safe under batched dispatch: a turn that pushes
    `tool_calls_used` from `limit - 2` to `limit + 3` in one provider
    response still fires on the next `dispatch_rules` evaluation, because
    the trigger checks "have we crossed an `every` boundary since last
    emission" rather than equality against a specific count.
    """

    def _trigger(messages: MessageList, context: "QueryContext") -> bool:
        del messages
        if context.tool_call_limit is None:
            return False
        over = context.tool_overshoot
        if over <= 0:
            return False
        state = context.notification_state.setdefault(
            _OVERFLOW_STATE_KEY, {"last_emitted_at": -1}
        )
        last = state["last_emitted_at"]
        if last < 0 or (over - last) >= every:
            state["last_emitted_at"] = over
            return True
        return False

    def _body(messages: MessageList, context: "QueryContext") -> str:
        del messages
        names = ", ".join(sorted(context.terminal_tools)) or "<terminal tool>"
        tolerance = context.max_tolerance_after_max_tool_call
        suffix = (
            f" Hard ceiling at {tolerance} overshoot units; you have used "
            f"{context.overshoot_units}."
            if tolerance is not None
            else ""
        )
        return (
            f"Tool-call budget exhausted ({context.tool_calls_used} / "
            f"{context.tool_call_limit}). Stop exploring and call a terminal "
            f"tool now to deliver your result: {names}.{suffix}"
        )

    return NotificationRule(
        name="budget_overflow_reminder",
        body=_body,
        trigger=_trigger,
        fire_once=False,
    )


def make_missing_terminal_reminder() -> NotificationRule:
    """Fire after a turn ends with text and no terminal call.

    The agent receives this nudge before the next provider turn so it can
    finish the run by calling a terminal tool. Does not fire when the agent
    has no terminal tools, has already delivered a terminal result, or just
    issued any tool call.
    """

    def _trigger(messages: MessageList, context: "QueryContext") -> bool:
        if not context.terminal_tools:
            return False
        if context.terminal_result is not None:
            return False
        for msg in reversed(messages):
            if msg.role != "assistant":
                continue
            return not msg.tool_uses
        return False

    def _body(messages: MessageList, context: "QueryContext") -> str:
        del messages
        names = ", ".join(sorted(context.terminal_tools))
        return (
            f"You returned plain text without calling a terminal tool. "
            f"Deliver your result via one of: {names}. "
            f"Do this now — no further exploration."
        )

    return NotificationRule(
        name="missing_terminal_reminder",
        body=_body,
        trigger=_trigger,
        fire_once=False,
    )
