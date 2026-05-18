"""Profile × failure-mode coverage for the retry nudge.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §1e rows 1-4.

Each registered profile has a distinct terminal-tool set. The retry
nudge produced by :func:`engine.agent.lifecycle._build_retry_nudge` must
mention every terminal tool name verbatim so the model knows which
submission tool to call. We parametrize over the live profile registry
so future profile additions are auto-covered.

The test runs the retry path through the lightweight
:class:`ScriptedRetryAgent` — we are testing nudge text and tool-call
limit semantics, not full agent spawn. The full spawn path is
``spawn_agent`` which requires DB-backed model registration.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents import load_agents_tree
from engine.agent.lifecycle import _build_retry_nudge, run_ephemeral_agent
from engine.query.context import QueryExitReason
from message.messages import TextBlock

from tests.unit_test.test_engine._retry_test_support import (
    ScriptedRetryAgent,
    install_scripted_agent,
    make_tool_result_user_message,
    terminal_completed_event,
)


PROFILE_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "agents"
    / "profile"
)


def _profile_definitions() -> list[Any]:
    """Load every registered profile; filter to those with non-empty terminals.

    Profiles without terminals (e.g., the dispatcher-style ``executor``
    entry-point) cannot trigger retry because the retry guard short-circuits
    on empty ``terminal_tools``. Skipping them keeps the matrix focused on
    real retry coverage.
    """
    return [d for d in load_agents_tree(PROFILE_DIR) if d.terminals]


PROFILES = _profile_definitions()
PROFILE_IDS = [d.name for d in PROFILES]


@pytest.mark.parametrize("profile_def", PROFILES, ids=PROFILE_IDS)
@pytest.mark.parametrize(
    "exit_reason",
    [QueryExitReason.RESOURCE_LIMIT, QueryExitReason.TEXT_RESPONSE],
    ids=["resource_limit", "text_response"],
)
def test_nudge_mentions_profile_terminal_tools(
    profile_def: Any, exit_reason: QueryExitReason
) -> None:
    """Each profile's terminal tool names must appear verbatim in the nudge."""
    terminals = set(profile_def.terminals)
    nudge = _build_retry_nudge(exit_reason, terminals)
    for tool_name in terminals:
        assert tool_name in nudge, (
            f"Nudge for profile {profile_def.name!r} on {exit_reason.value} "
            f"missing terminal {tool_name!r}: {nudge!r}"
        )


@pytest.mark.asyncio
async def test_retry_uses_profile_tool_call_limit_unchanged_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry resets ``tool_calls_used`` but never touches ``tool_call_limit``."""
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
        tool_call_limit=50,  # mimic evaluator/verifier's 50
    )
    # Simulate prior usage.
    agent.query_context.tool_calls_used = 50
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    # Attempt 1 saw used=50 (pre-seeded).
    assert agent.run_calls[0]["tool_calls_used_at_start"] == 50
    assert agent.run_calls[0]["tool_call_limit_at_start"] == 50
    # Attempt 2 saw used reset to 0; limit unchanged.
    assert agent.run_calls[1]["tool_calls_used_at_start"] == 0
    assert agent.run_calls[1]["tool_call_limit_at_start"] == 50


def test_handoff_profile_with_only_success_or_handoff_terminals() -> None:
    """executor_success_handoff has success + handoff but NOT failure terminal."""
    by_name = {d.name: d for d in PROFILES}
    handoff = by_name["executor_success_handoff"]
    assert set(handoff.terminals) == {
        "submit_execution_success",
        "submit_execution_handoff",
    }
    nudge = _build_retry_nudge(
        QueryExitReason.RESOURCE_LIMIT, set(handoff.terminals)
    )
    assert "submit_execution_success" in nudge
    assert "submit_execution_handoff" in nudge
    assert "submit_execution_failure" not in nudge


def test_planner_full_only_nudges_single_terminal() -> None:
    """planner_full_only has only submit_plan_closes_goal."""
    by_name = {d.name: d for d in PROFILES}
    full_only = by_name["planner_full_only"]
    assert set(full_only.terminals) == {"submit_plan_closes_goal"}
    nudge = _build_retry_nudge(
        QueryExitReason.TEXT_RESPONSE, set(full_only.terminals)
    )
    assert "submit_plan_closes_goal" in nudge
    # The other planner terminal must NOT leak in.
    assert "submit_plan_defers_goal" not in nudge


@pytest.mark.parametrize("profile_def", PROFILES, ids=PROFILE_IDS)
@pytest.mark.asyncio
async def test_retry_injects_profile_terminals_into_transcript(
    monkeypatch: pytest.MonkeyPatch, profile_def: Any
) -> None:
    """End-to-end: profile terminals reach the second-attempt transcript."""
    terminals = set(profile_def.terminals)
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools=terminals,
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    retry_messages = agent.run_calls[1]["messages_snapshot"]
    nudge_text = " ".join(
        block.text
        for msg in retry_messages
        if msg.role == "user"
        for block in msg.content
        if isinstance(block, TextBlock)
    )
    for tool_name in terminals:
        assert tool_name in nudge_text, (
            f"Profile {profile_def.name!r} retry transcript missing "
            f"terminal {tool_name!r}"
        )
