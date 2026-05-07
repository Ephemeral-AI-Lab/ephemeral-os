"""Soft reminder for resolver unresolved-count limit."""

from __future__ import annotations

from typing import Any

from notification.rules import NotificationRule
from tools.submission.resolver_history import unresolved_resolver_call_count


def make_resolver_limit_reminder(*, warning_at: int = 4) -> NotificationRule:
    def _trigger(messages: list[Any], context: Any) -> bool:
        del context
        return unresolved_resolver_call_count(messages) >= warning_at

    def _body(messages: list[Any], context: Any) -> str:
        del messages, context
        return (
            "One unresolved resolver call remains before success is blocked. "
            "Resolve and re-check the issues, or submit the failure terminal."
        )

    return NotificationRule(
        name="resolver_limit",
        trigger=_trigger,
        body=_body,
    )
