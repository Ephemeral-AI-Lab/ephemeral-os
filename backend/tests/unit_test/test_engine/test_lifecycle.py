from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest

from engine.agent.factory import EphemeralAgent
from engine.agent.lifecycle import run_ephemeral_agent
from engine.query.context import QueryContext, QueryExitReason
from message.messages import ConversationMessage, TextBlock
from message.stream_events import (
    AssistantMessageComplete,
    StreamEvent,
    ToolExecutionStarted,
)
from providers.types import ApiMessageCompleteEvent
from providers.types import UsageSnapshot
from tools._framework.core.base import ExecutionMetadata
from tools._framework.core.registry import ToolRegistry


class _FakeAgent:
    agent_name = "executor"
    model = "fake-model"
    total_usage = UsageSnapshot()
    _messages: list[Any] = []

    def __init__(self) -> None:
        self.query_context = SimpleNamespace(
            tool_metadata=ExecutionMetadata(),
            run_id="",
            terminal_result=None,
        )

    @property
    def messages(self) -> list[Any]:
        return self._messages

    async def run(self, _prompt: str):
        yield ToolExecutionStarted(
            tool_name="shell",
            tool_input={},
            agent_name=self.agent_name,
            run_id=self.query_context.run_id,
        )


@pytest.mark.asyncio
async def test_run_ephemeral_agent_stamps_task_id_as_stream_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeAgent()
    captured: list[StreamEvent] = []

    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_args, **_kwargs: fake_agent,
    )

    async def on_event(event: StreamEvent) -> None:
        captured.append(event)

    await run_ephemeral_agent(
        SimpleNamespace(),
        "repair this",
        task_id="run-1:t2",
        on_event=on_event,
    )

    assert fake_agent.query_context.run_id == "run-1:t2"
    assert captured == [
        ToolExecutionStarted(
            tool_name="shell",
            tool_input={},
            agent_name="executor",
            run_id="run-1:t2",
        )
    ]


@pytest.mark.asyncio
async def test_ephemeral_agent_run_preserves_initial_messages() -> None:
    class _TextClient:
        async def stream_message(self, request):
            assert [message.text for message in request.messages] == [
                "prior context",
                "new prompt",
            ]
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="done")],
                ),
                usage=UsageSnapshot(),
            )

    context = QueryContext(
        api_client=_TextClient(),
        tool_registry=ToolRegistry(),
        cwd=Path("/tmp"),
        model="test",
        system_prompt="",
        max_tokens=100,
        agent_name="executor",
        run_id="run-1:t1",
    )
    agent = EphemeralAgent(
        agent_name="executor",
        query_context=context,
        model="test",
        _messages=[ConversationMessage.from_user_text("prior context")],
    )

    completed: list[AssistantMessageComplete] = []
    async for event in agent.run("new prompt"):
        if isinstance(event, AssistantMessageComplete):
            completed.append(event)

    assert context.exit_reason is QueryExitReason.TEXT_RESPONSE
    assert [message.text for message in agent.messages] == [
        "prior context",
        "new prompt",
        "done",
    ]
    assert [(event.agent_name, event.run_id) for event in completed] == [
        ("executor", "run-1:t1")
    ]
