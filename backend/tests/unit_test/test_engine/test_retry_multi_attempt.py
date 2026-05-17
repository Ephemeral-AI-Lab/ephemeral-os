"""Multi-attempt sequences and edge cases.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §1d rows 1-5.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.agent.lifecycle import run_ephemeral_agent
from engine.query.context import QueryExitReason
from message.messages import ConversationMessage, TextBlock

from tests.unit_test.test_engine._retry_test_support import (
    ScriptedRetryAgent,
    install_scripted_agent,
    make_tool_result_user_message,
    terminal_completed_event,
)


@pytest.mark.asyncio
async def test_three_attempts_succeeds_on_second_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_terminal_retries=2 allows the third attempt to deliver terminal."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
            },
            {
                "events": [terminal_completed_event(output="done-on-3")],
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(
        SimpleNamespace(), "p", max_terminal_retries=2
    )

    assert result.status == "completed"
    assert result.terminal_result is not None
    assert result.terminal_result.output == "done-on-3"
    assert len(agent.run_calls) == 3
    # First call carries the prompt; retries carry None.
    assert agent.run_calls[0]["prompt"] == "p"
    assert agent.run_calls[1]["prompt"] is None
    assert agent.run_calls[2]["prompt"] is None


@pytest.mark.asyncio
async def test_three_attempts_all_fail_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three RESOURCE_LIMIT exits → terminal_result=None, status completed."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(
        SimpleNamespace(), "p", max_terminal_retries=2
    )

    assert result.status == "completed"
    assert result.terminal_result is None
    assert len(agent.run_calls) == 3


@pytest.mark.asyncio
async def test_alternating_exit_reasons_across_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RESOURCE_LIMIT → TEXT_RESPONSE → success; nudge text differs per kind."""
    assistant_text_reply = ConversationMessage(
        role="assistant", content=[TextBlock(text="just chatting")]
    )
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
                "append_messages": [assistant_text_reply],
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(
        SimpleNamespace(), "p", max_terminal_retries=2
    )

    assert result.status == "completed"
    assert result.terminal_result is not None
    assert len(agent.run_calls) == 3

    def _user_text_blocks(snapshot: list[ConversationMessage]) -> list[str]:
        return [
            block.text
            for msg in snapshot
            if msg.role == "user"
            for block in msg.content
            if isinstance(block, TextBlock)
        ]

    # Attempt 2 nudge: RESOURCE_LIMIT → "budget" wording present.
    attempt2_snapshot = agent.run_calls[1]["messages_snapshot"]
    attempt2_user_text = " ".join(_user_text_blocks(attempt2_snapshot))
    assert "budget" in attempt2_user_text.lower()
    assert "submit_x" in attempt2_user_text

    # Attempt 3 nudge: TEXT_RESPONSE → "plain text" wording present.
    attempt3_snapshot = agent.run_calls[2]["messages_snapshot"]
    attempt3_user_text = " ".join(_user_text_blocks(attempt3_snapshot))
    assert "plain text" in attempt3_user_text.lower()
    assert "submit_x" in attempt3_user_text


@pytest.mark.asyncio
async def test_crash_on_retry_attempt_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempt 1 fails (RESOURCE_LIMIT) → retry crashes → status=failed."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {"events": [], "raise": RuntimeError("retry-boom")},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    assert result.status == "failed"
    assert result.error == "retry-boom"
    assert result.terminal_result is None
    assert len(agent.run_calls) == 2  # crash short-circuits any further retry
    assert agent.close_calls == 1


@pytest.mark.asyncio
async def test_large_max_retries_does_not_run_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_terminal_retries=100 with always-fail script → exactly 101 attempts."""
    outcomes = [
        {
            "events": [],
            "exit_reason": QueryExitReason.RESOURCE_LIMIT,
            "append_messages": [make_tool_result_user_message()],
        }
        for _ in range(101)
    ]
    agent = ScriptedRetryAgent(
        outcomes=outcomes,
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(
        SimpleNamespace(), "p", max_terminal_retries=100
    )

    assert result.status == "completed"
    assert result.terminal_result is None
    # max_terminal_retries + 1 attempts maximum.
    assert len(agent.run_calls) == 101
    assert agent.close_calls == 1
