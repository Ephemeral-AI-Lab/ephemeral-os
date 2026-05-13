"""Notification rule dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from notification.rules.model import MessageList, NotificationRule

if TYPE_CHECKING:
    from engine.api import QueryContext
    from notification.runtime import SystemNotificationService


async def dispatch_rules(
    rules: list[NotificationRule],
    messages: MessageList,
    context: "QueryContext",
    service: "SystemNotificationService",
) -> None:
    """Evaluate `rules` in list order; emit each rule whose trigger fires.

    Called once per model turn from `_run_query_loop` before the next
    provider request is built. Rules fire in list order. Earlier rules'
    emissions land in the notification pool but do not appear in
    `messages` until the caller drains the pool, so a later rule's
    `trigger` cannot observe an earlier rule's reminder this turn.
    """
    fired = context.notification_fired
    for rule in rules:
        if rule.fire_once and rule.name in fired:
            continue
        if not rule.trigger(messages, context):
            continue
        text = rule.body(messages, context)
        if not text.strip():
            continue
        await service.notify_system(text)
        fired.add(rule.name)
