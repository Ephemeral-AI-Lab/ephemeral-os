"""Transcript pairing invariant for the TERMINAL_NOT_SUBMITTED exit.

When the loop hits the hard ceiling on a turn whose assistant message
produced ``tool_use`` blocks, the paired ``tool_result`` blocks must be
appended to the transcript before the loop breaks — the Anthropic
Messages API requires tool_use and tool_result blocks to be paired across
consecutive user/assistant messages.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from engine.query.context import QueryContext, QueryExitReason
from engine.query.loop import _run_query_loop
from engine.tool_call.dispatch import AssistantToolDispatchOutcome
from message.events import (
    AssistantMessageCompleteEvent,
    StreamEvent,
)
from message.message import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from providers.types import MessageRequest, UsageSnapshot
from tools._framework.core.base import ExecutionMetadata
from tools._framework.core.registry import ToolRegistry


class _OneTurnProvider:
    """Emits one assistant turn with two tool_uses then asserts no replay."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_message(
        self, request: MessageRequest
    ) -> AsyncIterator[StreamEvent]:
        del request
        self.calls += 1
        msg = Message(
            role="assistant",
            content=[
                ToolUseBlock(tool_use_id="tu_1", name="read_file", input={}),
                ToolUseBlock(tool_use_id="tu_2", name="read_file", input={}),
            ],
        )
        yield AssistantMessageCompleteEvent(message=msg, usage=UsageSnapshot())


@pytest.mark.asyncio
async def test_terminal_not_submitted_appends_paired_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hard exit must still pair the orphan tool_uses with tool_results."""
    limit = 4
    context = QueryContext(
        api_client=_OneTurnProvider(),
        tool_registry=ToolRegistry(),
        cwd=Path("/tmp"),
        model="test-model",
        system_prompt="",
        max_tokens=32,
        tool_call_limit=limit,
        tool_calls_used=math.ceil(1.5 * limit),  # already at the ceiling
        tool_metadata=ExecutionMetadata(),
        terminal_tools={"submit_x"},
    )
    messages: list[Message] = [Message.from_user_text("go")]

    dispatched_results = [
        ToolResultBlock(tool_use_id="tu_1", content="ok_1", is_error=False),
        ToolResultBlock(tool_use_id="tu_2", content="ok_2", is_error=False),
    ]

    async def _fake_dispatch(*_args: Any, **_kwargs: Any) -> AssistantToolDispatchOutcome:
        return AssistantToolDispatchOutcome(tool_results=dispatched_results)

    monkeypatch.setattr("engine.query.loop.dispatch_assistant_tools", _fake_dispatch)

    async for _ in _run_query_loop(context, messages):
        pass

    assert context.exit_reason == QueryExitReason.TERMINAL_NOT_SUBMITTED
    # Transcript ends with the tool_results paired to the orphan tool_uses.
    assert messages[-1].role == "user"
    last_content = messages[-1].content
    assert all(isinstance(block, ToolResultBlock) for block in last_content)
    assert [block.tool_use_id for block in last_content] == ["tu_1", "tu_2"]
    # The synthetic "Agent stopped" event is stream-only.
    assert not any(
        isinstance(block, TextBlock) and "terminal tool not submitted" in block.text
        for msg in messages
        for block in msg.content
    )
