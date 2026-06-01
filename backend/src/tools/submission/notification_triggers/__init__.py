"""Submission notification trigger factories."""

from __future__ import annotations

from notification import NotificationRule
from tools.submission.notification_triggers.nested_planner_deferral_disabled import (
    make_nested_planner_deferral_disabled_reminder,
)


def resolve_harness_notification_triggers(
    trigger_ids: list[str],
) -> list[NotificationRule]:
    factories = {
        "nested_planner_deferral_disabled": make_nested_planner_deferral_disabled_reminder,
    }
    rules: list[NotificationRule] = []
    for trigger_id in trigger_ids:
        factory = factories.get(trigger_id)
        if factory is None:
            raise ValueError(f"Unknown harness notification trigger {trigger_id!r}.")
        rules.append(factory())
    return rules


__all__ = [
    "make_nested_planner_deferral_disabled_reminder",
    "resolve_harness_notification_triggers",
]
