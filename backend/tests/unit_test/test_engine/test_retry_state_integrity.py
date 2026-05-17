"""Lifecycle bookkeeping across the new engine retry path.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §1c rows 1-8.

These assertions target the run-lifecycle wrapper in
:func:`run_ephemeral_agent` rather than the real query loop, so they use
the lightweight :class:`ScriptedRetryAgent` test double (no provider
client, no tool registry, no live ``run_query`` machinery).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from engine.agent.lifecycle import run_ephemeral_agent
from engine.query.context import QueryExitReason
from message.stream_events import ThinkingDelta
from providers.types import UsageSnapshot

from tests.unit_test.test_engine._retry_test_support import (
    ScriptedRetryAgent,
    install_scripted_agent,
    make_tool_result_user_message,
    terminal_completed_event,
)


def _make_thinking_event(text: str = "thinking") -> ThinkingDelta:
    return ThinkingDelta(text=text, agent_name="scripted", run_id="r-1")


@pytest.mark.asyncio
async def test_total_usage_accumulates_across_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``agent.total_usage`` must sum tokens from every attempt, not reset."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
                "usage": UsageSnapshot(input_tokens=10, output_tokens=4),
            },
            {
                "events": [terminal_completed_event()],
                "usage": UsageSnapshot(input_tokens=7, output_tokens=3),
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    assert agent.total_usage.input_tokens == 17
    assert agent.total_usage.output_tokens == 7


@pytest.mark.asyncio
async def test_close_called_exactly_once_after_all_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry path passes ``auto_close=False`` then closes once in finally."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    assert agent.close_calls == 1
    # And both attempts ran without auto_close so the client stays
    # alive across the retry boundary.
    assert all(call["auto_close"] is False for call in agent.run_calls)


@pytest.mark.asyncio
async def test_run_id_stable_across_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``query_context.run_id`` must be the same string on every attempt."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p", task_id="task-XYZ")

    assert agent.query_context.run_id == "task-XYZ"
    # Captured at the start of every attempt — must match.
    assert {call["run_id_at_start"] for call in agent.run_calls} == {"task-XYZ"}


@pytest.mark.asyncio
async def test_exit_reason_reset_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the start of attempt N+1, exit_reason must be None."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    # Attempt 1 enters with exit_reason=None.
    assert agent.run_calls[0]["exit_reason_at_start"] is None
    # Attempt 2 enters with exit_reason=None because the retry path
    # explicitly clears it before re-entering the run.
    assert agent.run_calls[1]["exit_reason_at_start"] is None


@pytest.mark.asyncio
async def test_budget_warning_state_cleared_per_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """budget_warning state from attempt 1 must NOT leak into attempt 2."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
        notification_state={
            "budget_warning": {"last_fired": 0.8, "pending_pct": 80}
        },
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    # Attempt 1 saw the pre-seeded state.
    assert agent.run_calls[0]["budget_warning_state_at_start"] == {
        "last_fired": 0.8,
        "pending_pct": 80,
    }
    # Attempt 2 saw an empty bookkeeping slot — the retry path pops the
    # ``budget_warning`` key so the rule re-arms.
    assert agent.run_calls[1]["budget_warning_state_at_start"] == {}


@pytest.mark.asyncio
async def test_other_notification_state_keys_preserved_across_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``budget_warning`` is dropped on retry — other keys must survive."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
        notification_state={
            "budget_warning": {"last_fired": 0.5},
            "opening_reminder": {"fired": True},
            "custom_rule_x": {"counter": 3},
        },
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    attempt2_state = agent.run_calls[1]["notification_state_at_start"]
    # budget_warning was dropped.
    assert "budget_warning" not in attempt2_state
    # Everything else is intact.
    assert attempt2_state.get("opening_reminder") == {"fired": True}
    assert attempt2_state.get("custom_rule_x") == {"counter": 3}


@pytest.mark.asyncio
async def test_tracker_finish_records_final_terminal_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AgentRunTracker.finish should be invoked exactly once with the final terminal."""
    finish_calls: list[dict[str, Any]] = []

    def _capture_finish(
        self: Any,
        *,
        messages: Any = None,
        terminal_tool_result: Any = None,
        token_count: int = 0,
        error: Any = None,
    ) -> None:
        finish_calls.append(
            {
                "terminal_tool_result": terminal_tool_result,
                "token_count": token_count,
                "error": error,
            }
        )

    monkeypatch.setattr(
        "engine.agent.run_tracker.AgentRunTracker.finish",
        _capture_finish,
        raising=True,
    )

    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
                "usage": UsageSnapshot(input_tokens=2, output_tokens=1),
            },
            {
                "events": [terminal_completed_event(output="final-result")],
                "usage": UsageSnapshot(input_tokens=4, output_tokens=2),
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    # Exactly one finish() call total across the entire run.
    assert len(finish_calls) == 1
    call = finish_calls[0]
    # The payload reflects the final terminal_result, not None or an
    # intermediate failure.
    assert call["terminal_tool_result"] is not None
    assert call["terminal_tool_result"]["output"] == "final-result"
    # Token count covers BOTH attempts (2+1 + 4+2 = 9).
    assert call["token_count"] == 9
    assert call["error"] is None
    assert result.terminal_result is not None
    assert result.terminal_result.output == "final-result"


@pytest.mark.asyncio
async def test_event_count_aggregates_across_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``EphemeralRunResult.event_count`` is the sum of events across attempts."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                # Attempt 1 emits 2 events before exiting.
                "events": [
                    _make_thinking_event("a"),
                    _make_thinking_event("b"),
                ],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                # Attempt 2 emits 3 events (one thinking + terminal).
                "events": [
                    _make_thinking_event("c"),
                    _make_thinking_event("d"),
                    terminal_completed_event(),
                ],
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    assert result.event_count == 5
