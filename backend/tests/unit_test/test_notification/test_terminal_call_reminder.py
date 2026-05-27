"""Unit tests for ``make_terminal_call_reminder``."""

from __future__ import annotations

import math
from typing import Any

import pytest

from message.message import Message, TextBlock
from notification import (
    SystemNotificationService,
    dispatch_rules,
    make_terminal_call_reminder,
)
from tools._framework.core.results import ToolResult


class _StubContext:
    """Minimal QueryContext stub used by the reminder body/trigger."""

    def __init__(
        self,
        *,
        terminal_result: ToolResult | None = None,
        tool_calls_used: int = 0,
        tool_call_limit: int = 10,
        terminal_tools: set[str] | None = None,
    ) -> None:
        self.terminal_result = terminal_result
        self.tool_calls_used = tool_calls_used
        self.tool_call_limit = tool_call_limit
        self.terminal_tools = terminal_tools or {"submit_x", "submit_y"}
        self.notification_fired: set[str] = set()
        self.notification_state: dict[str, Any] = {}


def _user_message(text: str = "hello") -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def _assistant_message(text: str = "ack") -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


@pytest.mark.asyncio
async def test_silent_on_opening_turn() -> None:
    """No assistant message yet → reminder must not fire."""
    rule = make_terminal_call_reminder()
    service = SystemNotificationService()
    ctx = _StubContext()

    await dispatch_rules([rule], [_user_message()], ctx, service)
    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_fires_after_assistant_message_without_terminal() -> None:
    rule = make_terminal_call_reminder()
    service = SystemNotificationService()
    ctx = _StubContext(tool_calls_used=3, tool_call_limit=10)
    messages = [_user_message(), _assistant_message()]

    await dispatch_rules([rule], messages, ctx, service)
    blocks = service.pop_pending_notifications()
    assert len(blocks) == 1
    text = blocks[0].text
    assert "submit_x" in text and "submit_y" in text
    assert "3/10" in text
    ceiling = math.ceil(1.5 * 10)
    assert str(ceiling) in text
    # turns_remaining = ceiling - used
    assert f"({ceiling - 3} remaining)" in text


@pytest.mark.asyncio
async def test_silent_once_terminal_result_set() -> None:
    rule = make_terminal_call_reminder()
    service = SystemNotificationService()
    ctx = _StubContext(
        terminal_result=ToolResult(output="done", is_error=False, is_terminal=True),
    )
    messages = [_user_message(), _assistant_message()]

    await dispatch_rules([rule], messages, ctx, service)
    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_body_includes_budget_and_ceiling_metrics() -> None:
    rule = make_terminal_call_reminder()
    service = SystemNotificationService()
    ctx = _StubContext(tool_calls_used=11, tool_call_limit=10)
    messages = [_user_message(), _assistant_message()]

    await dispatch_rules([rule], messages, ctx, service)
    body = service.pop_pending_notifications()[0].text
    assert "11/10" in body
    ceiling = math.ceil(1.5 * 10)
    assert str(ceiling) in body
    assert f"({max(0, ceiling - 11)} remaining)" in body
