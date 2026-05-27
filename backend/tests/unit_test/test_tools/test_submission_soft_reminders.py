"""Soft reminder tests for Phase 03 submission rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from message.message import Message, ToolResultBlock, ToolUseBlock
from notification import dispatch_rules
from notification import SystemNotificationService
from tools.submission.notification_triggers import (
    make_goal_request_after_edit_reminder,
    make_resolver_limit_reminder,
    resolve_harness_notification_triggers,
)

pytestmark = pytest.mark.asyncio


def _edit_messages() -> list[Message]:
    return [
        Message(
            role="assistant",
            content=[ToolUseBlock(tool_use_id="toolu_edit", name="shell", input={})],
        )
    ]


def _resolver_messages(count: int) -> list[Message]:
    messages: list[Message] = []
    for index in range(count):
        tool_use_id = f"toolu_resolver_{index}"
        messages.append(
            Message(
                role="assistant",
                content=[ToolUseBlock(tool_use_id=tool_use_id, name="ask_resolver", input={})],
            )
        )
        messages.append(
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=tool_use_id,
                        content="not resolved",
                        metadata={"resolver": {"resolved": False}},
                    )
                ],
            )
        )
    return messages


async def _dispatch(rule, messages, context):
    service = SystemNotificationService()
    context.notification_fired = set()
    await dispatch_rules([rule], messages, context, service)
    return service.pop_pending_notifications()


async def test_after_edit_reminder_fires_once() -> None:
    ctx = SimpleNamespace(tool_metadata=None, cwd="/tmp")

    notifications = await _dispatch(
        make_goal_request_after_edit_reminder(),
        _edit_messages(),
        ctx,
    )

    assert len(notifications) == 1
    assert "submit_execution_handoff is meant for delegating before edits begin" in notifications[0].text


async def test_resolver_limit_reminder_fires_at_four() -> None:
    ctx = SimpleNamespace(tool_metadata=None, cwd="/tmp")

    notifications = await _dispatch(
        make_resolver_limit_reminder(),
        _resolver_messages(4),
        ctx,
    )

    assert len(notifications) == 1
    assert "One unresolved resolver call remains" in notifications[0].text


async def test_resolve_harness_notification_triggers_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        resolve_harness_notification_triggers(["missing"])
