from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest

from engine.agent.factory import EphemeralAgent
from engine.agent.lifecycle import run_ephemeral_agent
from engine.query.context import QueryContext, QueryExitReason
from message.message import Message, TextBlock
from message.events import (
    AssistantMessageCompleteEvent,
    StreamEvent,
    ToolExecutionStartedEvent,
)
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
            agent_run_id="",
            terminal_result=None,
        )

    @property
    def messages(self) -> list[Any]:
        return self._messages

    async def run(self, _prompt, *, auto_close: bool = True):
        del auto_close
        yield ToolExecutionStartedEvent(
            tool_name="shell",
            tool_input={},
            agent_name=self.agent_name,
            agent_run_id=self.query_context.agent_run_id,
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_ephemeral_agent_stamps_agent_run_id_and_task_id(
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

    # The task id lands on its dedicated field; the stream identity is the
    # freshly minted agent_run_id (the agent_run_store is not ready in this
    # unit test, so the run is not persisted but the id is still minted).
    assert fake_agent.query_context.task_center_task_id == "run-1:t2"
    minted = fake_agent.query_context.agent_run_id
    assert minted and minted != "run-1:t2"
    assert captured == [
        ToolExecutionStartedEvent(
            tool_name="shell",
            tool_input={},
            agent_name="executor",
            agent_run_id=minted,
        )
    ]


@pytest.mark.asyncio
async def test_ephemeral_agent_run_preserves_initial_messages() -> None:
    class _TextClient:
        async def stream_message(self, request):
            assert [message.assistant_text for message in request.messages] == [
                "prior context",
                "new prompt",
            ]
            yield AssistantMessageCompleteEvent(
                message=Message(
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
        # Already past the hard ceiling, so the loop exits after one turn.
        tool_call_limit=1,
        tool_calls_used=2,
        terminal_tools={"submit_x"},
        agent_name="executor",
        agent_run_id="run-1:t1",
    )
    agent = EphemeralAgent(
        agent_name="executor",
        query_context=context,
        model="test",
        _messages=[Message.from_user_text("prior context")],
    )

    completed: list[AssistantMessageCompleteEvent] = []
    async for event in agent.run("new prompt"):
        if isinstance(event, AssistantMessageCompleteEvent):
            completed.append(event)

    # No terminal tool submitted; the hard ceiling triggers exit.
    assert context.exit_reason is QueryExitReason.TERMINAL_NOT_SUBMITTED
    assert [message.assistant_text for message in agent.messages] == [
        "prior context",
        "new prompt",
        "done",
    ]
    assert [(event.agent_name, event.agent_run_id) for event in completed] == [
        ("executor", "run-1:t1")
    ]
