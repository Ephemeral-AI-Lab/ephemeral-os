"""Coverage for the RESOURCE_LIMIT transcript fix in `_handle_tool_dispatch_branch`.

The retry path in :func:`run_ephemeral_agent` relies on the query loop leaving
a well-formed transcript when it cuts the agent off at the tool-call budget:
specifically, the partial tool_result blocks for the cut-off batch must land in
``messages`` so that the assistant's orphan tool_uses are paired.

This module exercises the loop branch directly with a mocked
``dispatch_assistant_tools`` to lock in that contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from engine.query.context import QueryContext, QueryExitReason
from engine.query.loop import _handle_tool_dispatch_branch
from engine.query.request import build_query_run_request
from engine.tool_call.dispatch import ToolDispatchResult
from message.messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from notification.runtime import SystemNotificationService
from tools._framework.core.base import ExecutionMetadata
from tools._framework.core.registry import ToolRegistry


def _build_context(*, used: int, limit: int) -> QueryContext:
    return QueryContext(
        api_client=MagicMock(),
        tool_registry=ToolRegistry(),
        cwd=Path("/tmp"),
        model="test-model",
        system_prompt="",
        max_tokens=32,
        tool_call_limit=limit,
        tool_calls_used=used,
        tool_metadata=ExecutionMetadata(),
        terminal_tools={"submit_x"},
    )


@pytest.mark.asyncio
async def test_resource_limit_exit_appends_tool_results_to_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hitting the budget while tool_uses are in flight must NOT orphan them.

    Before this fix, the loop returned early at the RESOURCE_LIMIT check
    without appending the dispatched tool_results, leaving the assistant's
    tool_use blocks unpaired. The retry path (and any follow-up provider
    call) would then fail with a malformed-transcript error.
    """
    context = _build_context(used=1, limit=1)
    assistant_tool_uses = [
        ToolUseBlock(id="tu_1", name="read_file", input={"path": "foo.txt"}),
        ToolUseBlock(id="tu_2", name="read_file", input={"path": "bar.txt"}),
    ]
    final_message = ConversationMessage(role="assistant", content=assistant_tool_uses)
    messages: list[ConversationMessage] = [
        ConversationMessage.from_user_text("do the thing"),
        final_message,
    ]

    dispatched_results = [
        ToolResultBlock(tool_use_id="tu_1", content="ok_1", is_error=False),
        ToolResultBlock(tool_use_id="tu_2", content="ok_2", is_error=False),
    ]

    async def _fake_dispatch(*_args: Any, **_kwargs: Any) -> ToolDispatchResult:
        return ToolDispatchResult(tool_results=dispatched_results)

    monkeypatch.setattr(
        "engine.query.loop.dispatch_assistant_tools", _fake_dispatch
    )

    state = MagicMock()
    state.final_message = final_message
    state.streamed_rejections = []
    state.streamed_tool_use_ids = set()

    run_request = build_query_run_request(context, messages)
    executor = MagicMock()
    notification_service = SystemNotificationService()

    branch = _handle_tool_dispatch_branch(
        context,
        messages,
        executor,
        run_request,
        state,
        background_manager=None,
        notification_service=notification_service,
    )
    # Drain the async generator so the branch runs to completion.
    async for _ in branch:
        pass

    assert context.exit_reason == QueryExitReason.RESOURCE_LIMIT
    # Transcript must end with the tool_results paired against the orphan
    # tool_use blocks in the assistant message above.
    assert messages[-1].role == "user"
    last_content = messages[-1].content
    assert all(isinstance(block, ToolResultBlock) for block in last_content)
    assert [block.tool_use_id for block in last_content] == ["tu_1", "tu_2"]
    # The synthetic "Agent stopped" event is stream-only — it must not
    # leak into the transcript.
    assert not any(
        isinstance(block, TextBlock) and "Agent stopped" in block.text
        for msg in messages
        for block in msg.content
    )
