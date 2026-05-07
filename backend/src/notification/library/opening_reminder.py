"""Opening-reminder rule factory.

Fires once at the start of an agent run (before any assistant message
exists) to inject a per-agent distilled rules block into the transcript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from notification.rules import MessageList, NotificationRule

if TYPE_CHECKING:
    from engine.api import QueryContext


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
