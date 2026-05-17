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
    ToolExecutionCompleted,
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

    async def run(self, _prompt, *, auto_close: bool = True):
        del auto_close
        yield ToolExecutionStarted(
            tool_name="shell",
            tool_input={},
            agent_name=self.agent_name,
            run_id=self.query_context.run_id,
        )

    async def close(self) -> None:
        return None


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


class _ScriptedRetryAgent:
    """Test double that scripts a sequence of run() outcomes.

    Each entry in ``outcomes`` is a list of stream events to yield for one
    attempt, plus an ``exit_reason`` and optional ``record_state`` callback
    invoked after the attempt's events drain so tests can snapshot the
    transcript / context per attempt.
    """

    agent_name = "scripted"
    model = "fake-model"

    def __init__(self, outcomes, *, terminal_tools=None):
        from providers.types import UsageSnapshot

        self.total_usage = UsageSnapshot()
        self._messages: list[ConversationMessage] = []
        self.outcomes = list(outcomes)
        self.run_calls: list[dict[str, Any]] = []
        self.close_calls = 0
        self.query_context = SimpleNamespace(
            tool_metadata=ExecutionMetadata(),
            run_id="",
            terminal_result=None,
            terminal_tools=set(terminal_tools or ()),
            tool_calls_used=0,
            tool_call_limit=None,
            exit_reason=None,
            notification_state={},
        )

    @property
    def messages(self) -> list[ConversationMessage]:
        return self._messages

    async def run(self, prompt, *, auto_close: bool = True):
        self.run_calls.append(
            {
                "prompt": prompt,
                "auto_close": auto_close,
                "messages_snapshot": list(self._messages),
                "tool_calls_used_at_start": self.query_context.tool_calls_used,
                "budget_warning_state_at_start": dict(
                    self.query_context.notification_state.get("budget_warning", {})
                ),
            }
        )
        if prompt is not None:
            self._messages = [
                *self._messages,
                ConversationMessage.from_user_text(prompt),
            ]
        if not self.outcomes:
            return
        outcome = self.outcomes.pop(0)
        for event in outcome.get("events", []):
            yield event
        if "raise" in outcome:
            raise outcome["raise"]
        # Simulate the production query loop appending transcript messages
        # before it returns control. RESOURCE_LIMIT exits append tool_results
        # as a user message; TEXT_RESPONSE exits append an assistant text.
        for message in outcome.get("append_messages", ()):
            self._messages = [*self._messages, message]
        self.query_context.exit_reason = outcome.get("exit_reason")
        if outcome.get("terminal_result") is not None:
            self.query_context.terminal_result = outcome["terminal_result"]

    async def close(self) -> None:
        self.close_calls += 1


def _terminal_event(output: str = "done") -> ToolExecutionCompleted:
    return ToolExecutionCompleted(
        tool_name="submit_x",
        output=output,
        is_error=False,
        does_terminate=True,
    )


@pytest.mark.asyncio
async def test_retry_on_resource_limit_then_terminal_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RESOURCE_LIMIT exit triggers one retry that delivers a terminal result."""
    tool_result_user_message = ConversationMessage(
        role="user", content=[TextBlock(text="dummy_tool_results")]
    )
    agent = _ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                # Production loop appends tool_results before returning on
                # RESOURCE_LIMIT — replicate that here so the retry path sees
                # the same well-formed transcript it sees in real runs.
                "append_messages": [tool_result_user_message],
            },
            {"events": [_terminal_event("delivered")]},
        ],
        terminal_tools={"submit_x"},
    )
    # Pre-seed budget-warning state so the retry path proves it gets cleared.
    agent.query_context.notification_state["budget_warning"] = {
        "last_fired": 0.9,
        "pending_pct": 90,
    }
    agent.query_context.tool_calls_used = 5

    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_a, **_kw: agent,
    )

    result = await run_ephemeral_agent(SimpleNamespace(), "do the thing")

    assert result.status == "completed"
    assert result.terminal_result is not None
    assert result.terminal_result.output == "delivered"
    assert len(agent.run_calls) == 2
    # First call carries the user prompt; retry carries None and resumes.
    assert agent.run_calls[0]["prompt"] == "do the thing"
    assert agent.run_calls[0]["auto_close"] is False
    assert agent.run_calls[1]["prompt"] is None
    assert agent.run_calls[1]["auto_close"] is False
    # Retry started with a fresh budget and a re-armed budget-warning rule.
    assert agent.run_calls[1]["tool_calls_used_at_start"] == 0
    assert agent.run_calls[1]["budget_warning_state_at_start"] == {}
    # Nudge merged into the existing tool_results user message rather than
    # appended as a fresh user message (which would stack two user turns).
    retry_transcript = agent.run_calls[1]["messages_snapshot"]
    # Transcript: [user("do the thing"), user(tool_results + nudge)].
    assert len(retry_transcript) == 2
    assert retry_transcript[-1].role == "user"
    nudge_blocks = [
        block
        for block in retry_transcript[-1].content
        if isinstance(block, TextBlock)
    ]
    block_texts = [block.text for block in nudge_blocks]
    assert "dummy_tool_results" in block_texts
    assert any("submit_x" in text for text in block_texts)
    assert any("budget" in text.lower() for text in block_texts)
    # close() ran exactly once after the final attempt.
    assert agent.close_calls == 1


@pytest.mark.asyncio
async def test_retry_on_text_response_appends_user_nudge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEXT_RESPONSE exit triggers retry; nudge is a fresh user message."""
    assistant_reply = ConversationMessage(
        role="assistant", content=[TextBlock(text="I'm done.")]
    )
    agent = _ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
                "append_messages": [assistant_reply],
            },
            {"events": [_terminal_event()]},
        ],
        terminal_tools={"submit_x", "submit_y"},
    )

    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_a, **_kw: agent,
    )

    result = await run_ephemeral_agent(SimpleNamespace(), "go")

    assert result.terminal_result is not None
    retry_transcript = agent.run_calls[1]["messages_snapshot"]
    # Transcript: [user("go"), assistant("I'm done."), user(nudge)].
    assert len(retry_transcript) == 3
    assert retry_transcript[-1].role == "user"
    nudge_text = " ".join(
        block.text for block in retry_transcript[-1].content if isinstance(block, TextBlock)
    )
    assert "submit_x" in nudge_text and "submit_y" in nudge_text
    assert "plain text" in nudge_text


@pytest.mark.asyncio
async def test_max_terminal_retries_zero_preserves_single_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max_terminal_retries=0`` disables the new retry path entirely."""
    agent = _ScriptedRetryAgent(
        outcomes=[
            {"events": [], "exit_reason": QueryExitReason.RESOURCE_LIMIT},
        ],
        terminal_tools={"submit_x"},
    )
    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_a, **_kw: agent,
    )

    result = await run_ephemeral_agent(
        SimpleNamespace(), "p", max_terminal_retries=0
    )

    assert result.status == "completed"
    assert result.terminal_result is None
    assert len(agent.run_calls) == 1
    assert agent.close_calls == 1


@pytest.mark.asyncio
async def test_retry_exhausted_returns_no_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every attempt fails to terminate, terminal_result is None."""
    agent = _ScriptedRetryAgent(
        outcomes=[
            {"events": [], "exit_reason": QueryExitReason.RESOURCE_LIMIT},
            {"events": [], "exit_reason": QueryExitReason.TEXT_RESPONSE},
            {"events": [], "exit_reason": QueryExitReason.RESOURCE_LIMIT},
        ],
        terminal_tools={"submit_x"},
    )
    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_a, **_kw: agent,
    )

    result = await run_ephemeral_agent(
        SimpleNamespace(), "p", max_terminal_retries=2
    )

    assert result.status == "completed"
    assert result.terminal_result is None
    assert len(agent.run_calls) == 3


@pytest.mark.asyncio
async def test_no_retry_without_terminal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent with no terminal tools cannot be nudged; no retry."""
    agent = _ScriptedRetryAgent(
        outcomes=[{"events": [], "exit_reason": QueryExitReason.TEXT_RESPONSE}],
        terminal_tools=set(),
    )
    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_a, **_kw: agent,
    )

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    assert result.status == "completed"
    assert result.terminal_result is None
    assert len(agent.run_calls) == 1


@pytest.mark.asyncio
async def test_crash_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions short-circuit the retry loop — only graceful exits retry."""
    agent = _ScriptedRetryAgent(
        outcomes=[
            {"events": [], "raise": RuntimeError("boom")},
            {"events": [_terminal_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_a, **_kw: agent,
    )

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    assert result.status == "failed"
    assert result.error == "boom"
    assert result.terminal_result is None
    assert len(agent.run_calls) == 1
    assert agent.close_calls == 1
