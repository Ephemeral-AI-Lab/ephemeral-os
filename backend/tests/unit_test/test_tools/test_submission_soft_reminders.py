"""Soft reminder tests for submission notification rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from notification import dispatch_rules
from notification import SystemNotificationService
import tools.submission.notification_triggers.nested_planner_deferral_disabled as planner_nested
from tools.submission.notification_triggers import (
    resolve_harness_notification_triggers,
)

pytestmark = pytest.mark.asyncio

async def _dispatch(rule, messages, context):
    service = SystemNotificationService()
    context.notification_fired = set()
    await dispatch_rules([rule], messages, context, service)
    return service.pop_pending_notifications()


async def test_nested_planner_deferral_reminder_fires_once(monkeypatch) -> None:
    monkeypatch.setattr(
        planner_nested,
        "tool_context_is_nested_workflow",
        lambda context: True,
    )
    context = SimpleNamespace(notification_fired=set(), tool_metadata=object())
    service = SystemNotificationService()
    rule = planner_nested.make_nested_planner_deferral_disabled_reminder()

    await dispatch_rules([rule], [], context, service)
    first = service.pop_pending_notifications()
    await dispatch_rules([rule], [], context, service)
    second = service.pop_pending_notifications()

    assert len(first) == 1
    assert "deferred_goal_for_next_iteration" in first[0].text
    assert second == []


async def test_resolve_harness_notification_triggers_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        resolve_harness_notification_triggers(["missing"])
