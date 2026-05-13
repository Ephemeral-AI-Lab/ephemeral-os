from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from engine.agent.lifecycle import run_ephemeral_agent
from message.stream_events import StreamEvent, ToolExecutionStarted
from providers.types import UsageSnapshot
from tools._framework.core.base import ExecutionMetadata


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
