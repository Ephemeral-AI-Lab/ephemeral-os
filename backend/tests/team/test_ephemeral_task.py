"""Unit tests for the ephemeral_task module."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ephemeral_task import (
    EDIT_CHECKPOINT_PROMPT,
    TURN_CHECKPOINT_PROMPT,
    EphemeralTaskResult,
    PauseVerdict,
    assess_pause,
    run_checkpoint,
)
from team.models import BudgetConfig, BudgetState, Note, Task, TaskStatus
from team.task_center import TaskCenter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_client(response_text: str) -> AsyncMock:
    """Create a mock API client that returns a canned response."""
    content_block = SimpleNamespace(text=response_text)
    response = SimpleNamespace(content=[content_block])
    client = AsyncMock()
    client.create_message = AsyncMock(return_value=response)
    return client


def _make_snapshot() -> list[dict]:
    return [
        {"role": "user", "content": "Fix the bug in parser.py"},
        {"role": "assistant", "content": "I'll look at parser.py now."},
    ]


class _FakeSessionFactory:
    def __call__(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None
            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


def _tc(**kwargs) -> TaskCenter:
    defaults = dict(
        session_factory=_FakeSessionFactory(),
        team_run_id="run-1",
        budgets=BudgetConfig(),
        budget_state=BudgetState(),
    )
    defaults.update(kwargs)
    return TaskCenter(**defaults)


# ---------------------------------------------------------------------------
# run_checkpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_checkpoint_returns_text():
    client = _make_api_client("Edited parser.py to fix indentation bug.")
    result = await run_checkpoint(
        snapshot=_make_snapshot(),
        prompt=EDIT_CHECKPOINT_PROMPT,
        api_client=client,
    )
    assert isinstance(result, EphemeralTaskResult)
    assert result.text == "Edited parser.py to fix indentation bug."
    assert not result.timed_out
    assert result.elapsed_seconds >= 0

    # Verify prompt was sent as user message
    call_kwargs = client.create_message.call_args.kwargs
    messages = call_kwargs["messages"]
    last_msg = messages[-1]
    assert last_msg["role"] == "user"
    assert "progress note" in last_msg["content"].lower()


@pytest.mark.asyncio
async def test_run_checkpoint_timeout():
    client = AsyncMock()
    client.create_message = AsyncMock(side_effect=asyncio.TimeoutError())
    result = await run_checkpoint(
        snapshot=_make_snapshot(),
        prompt="summarize",
        api_client=client,
        timeout_seconds=1,
    )
    assert result.timed_out
    assert result.text == ""


@pytest.mark.asyncio
async def test_run_checkpoint_exception_returns_empty():
    client = AsyncMock()
    client.create_message = AsyncMock(side_effect=RuntimeError("api down"))
    result = await run_checkpoint(
        snapshot=_make_snapshot(),
        prompt="summarize",
        api_client=client,
    )
    assert not result.timed_out
    assert result.text == ""


# ---------------------------------------------------------------------------
# assess_pause tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assess_pause_yes():
    client = _make_api_client("YES: my task imports compatibility.py")
    verdict = await assess_pause(
        task_id="t1",
        snapshot=_make_snapshot(),
        system_prompt="You are a developer.",
        broken_files=["dask/compatibility.py"],
        problem="ImportError on parse",
        api_client=client,
    )
    assert isinstance(verdict, PauseVerdict)
    assert verdict.answer == "YES"
    assert "compatibility" in verdict.reason


@pytest.mark.asyncio
async def test_assess_pause_no():
    client = _make_api_client("NO: I only work on bag/ files")
    verdict = await assess_pause(
        task_id="t2",
        snapshot=_make_snapshot(),
        system_prompt="You are a developer.",
        broken_files=["dask/compatibility.py"],
        problem="ImportError",
        api_client=client,
    )
    assert verdict.answer == "NO"


@pytest.mark.asyncio
async def test_assess_pause_timeout():
    client = AsyncMock()
    client.create_message = AsyncMock(side_effect=asyncio.TimeoutError())
    verdict = await assess_pause(
        task_id="t3",
        snapshot=_make_snapshot(),
        system_prompt="sys",
        broken_files=["x.py"],
        problem="broken",
        api_client=client,
        timeout_seconds=1,
    )
    assert verdict.answer == "TIMEOUT"


# ---------------------------------------------------------------------------
# TaskCenter.check() with EphemeralTask integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_uses_llm_when_snapshot_provided():
    tc = _tc()
    # Seed a task in graph
    tc.graph["t1"] = Task(
        id="t1", team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    # Cross edit threshold
    for i in range(5):
        tc.on_edit("t1", f"file{i}.py")

    client = _make_api_client("Edited 5 files to fix parser indentation.")
    snapshot = _make_snapshot()

    result = await tc.check("t1", snapshot=snapshot, api_client=client)
    assert result is True

    # Note should contain LLM-generated content
    notes = await tc.read()
    assert len(notes) == 1
    assert notes[0].content == "Edited 5 files to fix parser indentation."
    assert notes[0].agent_name == "developer (auto)"


@pytest.mark.asyncio
async def test_check_falls_back_to_factual_without_snapshot():
    tc = _tc()
    tc.graph["t1"] = Task(
        id="t1", team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    for i in range(5):
        tc.on_edit("t1", f"file{i}.py")

    result = await tc.check("t1")
    assert result is True

    notes = await tc.read()
    assert len(notes) == 1
    assert "Auto-checkpoint" in notes[0].content
    assert "5 edits" in notes[0].content


@pytest.mark.asyncio
async def test_check_falls_back_on_empty_llm_response():
    tc = _tc()
    tc.graph["t1"] = Task(
        id="t1", team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    for _ in range(10):
        tc.tick("t1")

    client = _make_api_client("")  # Empty response
    result = await tc.check("t1", snapshot=_make_snapshot(), api_client=client)
    assert result is True

    notes = await tc.read()
    assert "Auto-checkpoint" in notes[0].content
    assert "10 turns" in notes[0].content


@pytest.mark.asyncio
async def test_check_no_threshold_crossed():
    tc = _tc()
    tc.graph["t1"] = Task(
        id="t1", team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    tc.on_edit("t1", "a.py")  # Only 1 edit, threshold is 5

    result = await tc.check("t1")
    assert result is False
    assert len(await tc.read()) == 0


@pytest.mark.asyncio
async def test_check_selects_turn_prompt_for_turn_trigger():
    tc = _tc()
    tc.graph["t1"] = Task(
        id="t1", team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    for _ in range(10):
        tc.tick("t1")

    client = _make_api_client("Agent is working on tests, no blockers.")
    await tc.check("t1", snapshot=_make_snapshot(), api_client=client)

    # Verify the turn prompt was used (not edit prompt)
    call_kwargs = client.create_message.call_args.kwargs
    last_msg = call_kwargs["messages"][-1]
    assert "Current status" in last_msg["content"]


@pytest.mark.asyncio
async def test_check_resets_counters_after_note():
    tc = _tc()
    tc.graph["t1"] = Task(
        id="t1", team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    for i in range(5):
        tc.on_edit("t1", f"file{i}.py")

    await tc.check("t1")

    # Counters should be reset after note posted
    assert tc.should_checkpoint("t1") is None
