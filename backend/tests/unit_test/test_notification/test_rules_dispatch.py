"""Unit tests for NotificationRule and dispatch_rules."""

from __future__ import annotations

from typing import Any

import pytest

from notification import NotificationRule, dispatch_rules
from notification import SystemNotificationService


class _StubContext:
    """Minimal duck-typed QueryContext stand-in for rule unit tests."""

    def __init__(self) -> None:
        self.notification_fired: set[str] = set()
        self.notification_state: dict[str, Any] = {}


def _rule(
    name: str,
    *,
    body: str = "hi",
    fires: bool = True,
    fire_once: bool = True,
) -> NotificationRule:
    return NotificationRule(
        name=name,
        body=lambda _msgs, _ctx, _b=body: _b,
        trigger=lambda _msgs, _ctx, _f=fires: _f,
        fire_once=fire_once,
    )


@pytest.mark.asyncio
async def test_dispatch_emits_when_trigger_fires() -> None:
    service = SystemNotificationService()
    context = _StubContext()
    rule = _rule("greeting")

    await dispatch_rules([rule], [], context, service)

    blocks = service.pop_pending_notifications()
    assert [b.text for b in blocks] == ["hi"]
    assert context.notification_fired == {"greeting"}


@pytest.mark.asyncio
async def test_dispatch_suppresses_when_trigger_returns_false() -> None:
    service = SystemNotificationService()
    context = _StubContext()
    rule = _rule("silent", fires=False)

    await dispatch_rules([rule], [], context, service)

    assert service.pop_pending_notifications() == []
    assert context.notification_fired == set()


@pytest.mark.asyncio
async def test_dispatch_suppresses_empty_body() -> None:
    service = SystemNotificationService()
    context = _StubContext()
    rule = _rule("blank", body="   ")  # whitespace-only body

    await dispatch_rules([rule], [], context, service)

    assert service.pop_pending_notifications() == []
    # Body suppression also prevents marking the rule as fired so a future
    # turn (with a non-empty body) can still emit.
    assert context.notification_fired == set()


@pytest.mark.asyncio
async def test_dispatch_respects_fire_once() -> None:
    service = SystemNotificationService()
    context = _StubContext()
    rule = _rule("only_once", fire_once=True)

    await dispatch_rules([rule], [], context, service)
    await dispatch_rules([rule], [], context, service)

    blocks = service.pop_pending_notifications()
    assert len(blocks) == 1
    assert context.notification_fired == {"only_once"}


@pytest.mark.asyncio
async def test_dispatch_repeats_when_fire_once_false() -> None:
    service = SystemNotificationService()
    context = _StubContext()
    rule = _rule("recurring", fire_once=False)

    await dispatch_rules([rule], [], context, service)
    await dispatch_rules([rule], [], context, service)

    blocks = service.pop_pending_notifications()
    assert len(blocks) == 2
    # `fired` tracks every emission for observability; the `fire_once`
    # flag is what controls whether membership in `fired` skips subsequent
    # invocations. fire_once=False rules manage their own dedup via
    # context.notification_state if they need it.
    assert context.notification_fired == {"recurring"}


@pytest.mark.asyncio
async def test_dispatch_iterates_in_list_order() -> None:
    service = SystemNotificationService()
    context = _StubContext()
    rules = [
        _rule("first", body="A"),
        _rule("second", body="B"),
    ]

    await dispatch_rules(rules, [], context, service)

    blocks = service.pop_pending_notifications()
    assert [b.text for b in blocks] == ["A", "B"]


@pytest.mark.asyncio
async def test_dispatch_does_not_expose_same_turn_notifications_to_later_rules() -> None:
    service = SystemNotificationService()
    context = _StubContext()
    messages = []
    rules = [
        _rule("first", body="A"),
        NotificationRule(
            name="second",
            body=lambda _msgs, _ctx: "B",
            trigger=lambda msgs, _ctx: any(
                block.text == "A"
                for message in msgs
                for block in message.content
            ),
        ),
    ]

    await dispatch_rules(rules, messages, context, service)

    blocks = service.pop_pending_notifications()
    assert [b.text for b in blocks] == ["A"]
    assert context.notification_fired == {"first"}
