"""Loop-level coverage for the Phase 2 soft-limit + tolerance model.

Exercises the integration between ``_dispatch_final_message_tools`` and
``_run_query_loop``'s text-only path: hard-cap exit lives at
``overshoot_units > tolerance`` and is reachable via either contributor
(tool overflow or text-only-no-terminal). The transcript stays well-formed
at every exit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from engine.query.context import QueryContext, QueryExitReason
from engine.query.loop import _dispatch_final_message_tools
from engine.query.request import build_query_run_request
from engine.tool_call.dispatch import AssistantToolDispatchOutcome
from message.messages import (
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
)
from notification.runtime import SystemNotificationService
from tools._framework.core.base import ExecutionMetadata
from tools._framework.core.registry import ToolRegistry
from tools._framework.core.results import ToolResult


def _build_context(
    *,
    used: int,
    limit: int | None,
    tolerance: int | None,
    text_turns: int = 0,
) -> QueryContext:
    return QueryContext(
        api_client=MagicMock(),
        tool_registry=ToolRegistry(),
        cwd=Path("/tmp"),
        model="test-model",
        system_prompt="",
        max_tokens=32,
        tool_call_limit=limit,
        tool_calls_used=used,
        max_tolerance_after_max_tool_call=tolerance,
        text_only_no_terminal_turns=text_turns,
        tool_metadata=ExecutionMetadata(),
        terminal_tools={"submit_x"},
    )


async def _drive_dispatch_branch(
    context: QueryContext,
    messages: list[ConversationMessage],
    final_message: ConversationMessage,
    dispatched_results: list[ToolResultBlock],
    monkeypatch: pytest.MonkeyPatch,
    terminal_result: ToolResult | None = None,
) -> None:
    async def _fake_dispatch(*_args: Any, **_kwargs: Any) -> AssistantToolDispatchOutcome:
        return AssistantToolDispatchOutcome(
            tool_results=dispatched_results,
            terminal_result=terminal_result,
        )

    monkeypatch.setattr("engine.query.loop.dispatch_assistant_tools", _fake_dispatch)

    state = MagicMock()
    state.final_message = final_message
    state.streamed_tool_use_ids = set()

    run_request = build_query_run_request(context, messages)
    executor = MagicMock()
    notification_service = SystemNotificationService()

    branch = _dispatch_final_message_tools(
        context,
        messages,
        executor,
        run_request,
        state,
        background_tasks=None,
        notification_service=notification_service,
    )
    async for _ in branch:
        pass


@pytest.mark.asyncio
async def test_dispatch_continues_when_overshoot_at_tolerance_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`overshoot_units == tolerance` is allowed; only `> tolerance` exits.

    With limit=10, tolerance=5, used=14 (overshoot=4), a single new tool
    call bumps used to 15 (overshoot=5) — exactly at the boundary, NOT
    yet over. The loop must NOT set RESOURCE_LIMIT.
    """
    context = _build_context(used=14, limit=10, tolerance=5)
    tool_uses = [ToolUseBlock(id="tu_1", name="read_file", input={})]
    final_message = ConversationMessage(role="assistant", content=tool_uses)
    messages: list[ConversationMessage] = [
        ConversationMessage.from_user_text("go"),
        final_message,
    ]
    dispatched_results = [ToolResultBlock(tool_use_id="tu_1", content="ok", is_error=False)]
    # Simulate the per-tool counter bump that happens in
    # ``_count_tool_dispatch`` before dispatch returns.
    context.tool_calls_used += 1

    await _drive_dispatch_branch(
        context, messages, final_message, dispatched_results, monkeypatch
    )

    assert context.tool_overshoot == 5
    assert context.overshoot_units == 5
    assert context.exit_reason is None  # 5 > 5 is False, loop continues


@pytest.mark.asyncio
async def test_dispatch_exits_resource_limit_when_overshoot_exceeds_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One tool call past the boundary trips the hard exit."""
    context = _build_context(used=15, limit=10, tolerance=5)
    tool_uses = [ToolUseBlock(id="tu_1", name="read_file", input={})]
    final_message = ConversationMessage(role="assistant", content=tool_uses)
    messages: list[ConversationMessage] = [
        ConversationMessage.from_user_text("go"),
        final_message,
    ]
    dispatched_results = [ToolResultBlock(tool_use_id="tu_1", content="ok", is_error=False)]
    # Simulate the per-tool counter bump.
    context.tool_calls_used += 1

    await _drive_dispatch_branch(
        context, messages, final_message, dispatched_results, monkeypatch
    )

    assert context.tool_overshoot == 6
    assert context.overshoot_units == 6
    assert context.exit_reason == QueryExitReason.RESOURCE_LIMIT
    # Transcript still pairs orphan tool_use blocks with tool_result blocks.
    assert messages[-1].role == "user"
    assert all(isinstance(block, ToolResultBlock) for block in messages[-1].content)


@pytest.mark.asyncio
async def test_dispatch_text_only_contributes_to_shared_overshoot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed mode: tool overshoot + text-only turns share one tolerance budget.

    limit=10, tolerance=2. Pre-state: used=11 (tool_overshoot=1) AND
    text_only_no_terminal_turns=2 → overshoot_units = 1 + 2 = 3 > 2. The
    next tool dispatch hits the loop's hard-cap check and exits via
    RESOURCE_LIMIT (the contributor that tipped the cap was tool-path —
    here it's already past; one more tool call makes it 4 > 2).
    """
    context = _build_context(used=11, limit=10, tolerance=2, text_turns=2)
    tool_uses = [ToolUseBlock(id="tu_1", name="read_file", input={})]
    final_message = ConversationMessage(role="assistant", content=tool_uses)
    messages: list[ConversationMessage] = [
        ConversationMessage.from_user_text("go"),
        final_message,
    ]
    dispatched_results = [ToolResultBlock(tool_use_id="tu_1", content="ok", is_error=False)]
    context.tool_calls_used += 1  # simulated dispatch counter bump

    await _drive_dispatch_branch(
        context, messages, final_message, dispatched_results, monkeypatch
    )

    # overshoot_units = (12-10) + 2 = 4 > 2.
    assert context.overshoot_units == 4
    assert context.exit_reason == QueryExitReason.RESOURCE_LIMIT


@pytest.mark.asyncio
async def test_dispatch_no_tolerance_means_no_hard_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max_tolerance_after_max_tool_call=None`` disables the hard exit entirely."""
    context = _build_context(used=100, limit=10, tolerance=None)
    tool_uses = [ToolUseBlock(id="tu_1", name="read_file", input={})]
    final_message = ConversationMessage(role="assistant", content=tool_uses)
    messages: list[ConversationMessage] = [
        ConversationMessage.from_user_text("go"),
        final_message,
    ]
    dispatched_results = [ToolResultBlock(tool_use_id="tu_1", content="ok", is_error=False)]
    context.tool_calls_used += 1

    await _drive_dispatch_branch(
        context, messages, final_message, dispatched_results, monkeypatch
    )

    assert context.tool_overshoot == 91
    assert context.exit_reason is None


@pytest.mark.asyncio
async def test_dispatch_terminal_result_short_circuits_overshoot_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal result wins even when overshoot > tolerance.

    The loop's check order: terminal_result -> overshoot. A successful
    terminal call delivers the run regardless of how many calls the
    agent burned getting there.
    """
    context = _build_context(used=100, limit=10, tolerance=5)
    tool_uses = [ToolUseBlock(id="tu_1", name="submit_x", input={})]
    final_message = ConversationMessage(role="assistant", content=tool_uses)
    messages: list[ConversationMessage] = [
        ConversationMessage.from_user_text("go"),
        final_message,
    ]
    dispatched_results = [
        ToolResultBlock(
            tool_use_id="tu_1",
            content="delivered",
            is_error=False,
            does_terminate=True,
        )
    ]
    terminal_result = ToolResult(
        output="delivered", is_error=False, does_terminate=True
    )
    context.tool_calls_used += 1

    await _drive_dispatch_branch(
        context,
        messages,
        final_message,
        dispatched_results,
        monkeypatch,
        terminal_result=terminal_result,
    )

    assert context.exit_reason == QueryExitReason.TOOL_STOP
    assert context.terminal_result is not None
    assert context.terminal_result.output == "delivered"
