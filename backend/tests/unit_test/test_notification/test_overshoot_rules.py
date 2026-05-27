"""Unit tests for budget_overflow + missing_terminal notification rules."""

from __future__ import annotations

from typing import Any

import pytest

from message.message import Message, TextBlock, ToolUseBlock
from notification import (
    SystemNotificationService,
    dispatch_rules,
    make_budget_overflow_reminder,
    make_missing_terminal_reminder,
)


class _StubContext:
    """QueryContext stub exposing the surface used by overshoot rules."""

    def __init__(
        self,
        *,
        tool_call_limit: int | None = 10,
        tool_calls_used: int = 0,
        text_only_no_terminal_turns: int = 0,
        max_tolerance_after_max_tool_call: int | None = 5,
        terminal_tools: set[str] | None = None,
        terminal_result: Any = None,
    ) -> None:
        self.tool_call_limit = tool_call_limit
        self.tool_calls_used = tool_calls_used
        self.text_only_no_terminal_turns = text_only_no_terminal_turns
        self.max_tolerance_after_max_tool_call = max_tolerance_after_max_tool_call
        self.terminal_tools = terminal_tools if terminal_tools is not None else {"submit_x"}
        self.terminal_result = terminal_result
        self.notification_fired: set[str] = set()
        self.notification_state: dict[str, Any] = {}

    @property
    def tool_overshoot(self) -> int:
        if self.tool_call_limit is None:
            return 0
        return max(0, self.tool_calls_used - self.tool_call_limit)

    @property
    def overshoot_units(self) -> int:
        return self.tool_overshoot + self.text_only_no_terminal_turns


def _user(text: str = "go") -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def _assistant_text(text: str = "ok") -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


def _assistant_tool_use() -> Message:
    return Message(
        role="assistant",
        content=[ToolUseBlock(tool_use_id="t1", name="read_file", input={"path": "f.txt"})],
    )


# ---------- make_budget_overflow_reminder ------------------------------------


@pytest.mark.asyncio
async def test_overflow_reminder_silent_below_limit() -> None:
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(tool_call_limit=10, tool_calls_used=9)

    await dispatch_rules([rule], [_user()], ctx, service)

    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_overflow_reminder_silent_at_limit_no_overshoot() -> None:
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(tool_call_limit=10, tool_calls_used=10)

    await dispatch_rules([rule], [_user()], ctx, service)

    # tool_overshoot == 0 → rule does not fire yet (only fires once positive).
    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_overflow_reminder_fires_on_first_overshoot() -> None:
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(tool_call_limit=10, tool_calls_used=11)

    await dispatch_rules([rule], [_user()], ctx, service)

    blocks = service.pop_pending_notifications()
    assert len(blocks) == 1
    assert "Tool-call budget exhausted" in blocks[0].text
    assert "submit_x" in blocks[0].text


@pytest.mark.asyncio
async def test_overflow_reminder_does_not_refire_within_every() -> None:
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(tool_call_limit=10, tool_calls_used=11)

    await dispatch_rules([rule], [_user()], ctx, service)
    assert len(service.pop_pending_notifications()) == 1

    # Walk overshoot 2..5 — within 5 of last_emitted (1).
    for used in (12, 13, 14, 15):
        ctx.tool_calls_used = used
        await dispatch_rules([rule], [_user()], ctx, service)
        assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_overflow_reminder_refires_at_every_boundary() -> None:
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(tool_call_limit=10, tool_calls_used=11)

    await dispatch_rules([rule], [_user()], ctx, service)
    assert len(service.pop_pending_notifications()) == 1

    ctx.tool_calls_used = 16  # overshoot=6, 6 - 1 = 5 >= 5 → fires
    await dispatch_rules([rule], [_user()], ctx, service)
    assert len(service.pop_pending_notifications()) == 1


@pytest.mark.asyncio
async def test_overflow_reminder_fires_after_batched_jump() -> None:
    """Architect revision #1: batched overshoot must still fire first reminder.

    `tool_calls_used` jumps from `limit - 2` to `limit + 5` in one provider
    turn. On the next `dispatch_rules` evaluation `tool_overshoot == 5`. With
    the monotonic-crossing-safe trigger, the first crossing still fires.
    """
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(tool_call_limit=10, tool_calls_used=15)  # over=5

    await dispatch_rules([rule], [_user()], ctx, service)

    blocks = service.pop_pending_notifications()
    assert len(blocks) == 1


@pytest.mark.asyncio
async def test_overflow_reminder_no_limit_silent() -> None:
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(tool_call_limit=None, tool_calls_used=999)

    await dispatch_rules([rule], [_user()], ctx, service)

    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_overflow_reminder_body_includes_tolerance_suffix() -> None:
    rule = make_budget_overflow_reminder(every=5)
    service = SystemNotificationService()
    ctx = _StubContext(
        tool_call_limit=10,
        tool_calls_used=12,
        max_tolerance_after_max_tool_call=5,
    )

    await dispatch_rules([rule], [_user()], ctx, service)

    blocks = service.pop_pending_notifications()
    assert "Hard ceiling at 5 overshoot units" in blocks[0].text
    assert "you have used 2" in blocks[0].text


# ---------- make_missing_terminal_reminder -----------------------------------


@pytest.mark.asyncio
async def test_missing_terminal_fires_after_text_only_assistant() -> None:
    rule = make_missing_terminal_reminder()
    service = SystemNotificationService()
    ctx = _StubContext()

    messages = [_user(), _assistant_text()]
    await dispatch_rules([rule], messages, ctx, service)

    blocks = service.pop_pending_notifications()
    assert len(blocks) == 1
    assert "without calling a terminal tool" in blocks[0].text
    assert "submit_x" in blocks[0].text


@pytest.mark.asyncio
async def test_missing_terminal_silent_after_tool_use() -> None:
    rule = make_missing_terminal_reminder()
    service = SystemNotificationService()
    ctx = _StubContext()

    messages = [_user(), _assistant_tool_use()]
    await dispatch_rules([rule], messages, ctx, service)

    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_missing_terminal_silent_when_terminal_already_delivered() -> None:
    rule = make_missing_terminal_reminder()
    service = SystemNotificationService()
    ctx = _StubContext(terminal_result=object())  # any truthy stand-in

    messages = [_user(), _assistant_text()]
    await dispatch_rules([rule], messages, ctx, service)

    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_missing_terminal_silent_without_terminal_tools() -> None:
    rule = make_missing_terminal_reminder()
    service = SystemNotificationService()
    ctx = _StubContext(terminal_tools=set())

    messages = [_user(), _assistant_text()]
    await dispatch_rules([rule], messages, ctx, service)

    assert service.pop_pending_notifications() == []


@pytest.mark.asyncio
async def test_missing_terminal_silent_with_no_assistant_yet() -> None:
    rule = make_missing_terminal_reminder()
    service = SystemNotificationService()
    ctx = _StubContext()

    messages = [_user()]  # opening turn — no assistant message
    await dispatch_rules([rule], messages, ctx, service)

    assert service.pop_pending_notifications() == []
