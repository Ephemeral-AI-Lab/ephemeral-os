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
    NoteSummary,
    PauseVerdict,
    Snapshot,
    assess_pause,
    run_ephemeral_note,
)
from team.models import BudgetConfig, BudgetState, Note, Task, TaskStatus
from team.task_center import TaskCenter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_response(tool_name: str, tool_input: dict) -> SimpleNamespace:
    """Create a mock API response with a tool_use content block."""
    block = SimpleNamespace(type="tool_use", name=tool_name, input=tool_input)
    return SimpleNamespace(content=[block])


def _make_text_response(text: str) -> SimpleNamespace:
    """Create a mock API response with a text content block."""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block])


def _make_api_client_tool(tool_name: str, tool_input: dict) -> AsyncMock:
    """Create a mock API client that returns a forced tool call response."""
    response = _make_tool_response(tool_name, tool_input)
    client = AsyncMock()
    client.create_message = AsyncMock(return_value=response)
    return client


def _make_api_client(response_text: str) -> AsyncMock:
    """Create a mock API client that returns a text response (for fallback/legacy)."""
    response = _make_text_response(response_text)
    client = AsyncMock()
    client.create_message = AsyncMock(return_value=response)
    return client


def _make_snapshot(task_id: str = "t1") -> Snapshot:
    return Snapshot(
        task_id=task_id,
        agent_run_id=f"{task_id}-run",
        messages=[
            {"role": "user", "content": "Fix the bug in parser.py"},
            {"role": "assistant", "content": "I'll look at parser.py now."},
        ],
        system_prompt="You are a developer.",
    )


def _make_raw_messages() -> list[dict]:
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
# run_checkpoint tests (forced tool call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_checkpoint_returns_structured_note():
    client = _make_api_client_tool("post_note", {
        "note": "Edited parser.py to fix indentation bug.",
        "status": "working",
    })
    snap = _make_snapshot()
    result = await run_ephemeral_note(
        snapshot=snap,
        prompt=EDIT_CHECKPOINT_PROMPT,
        api_client=client,
    )
    assert isinstance(result, NoteSummary)
    assert result.text == "Edited parser.py to fix indentation bug."
    assert result.status == "working"
    assert not result.timed_out
    assert result.elapsed_seconds >= 0

    # Verify tool was passed with tool_choice="any"
    call_kwargs = client.create_message.call_args.kwargs
    assert call_kwargs["tool_choice"] == {"type": "any"}
    assert len(call_kwargs["tools"]) == 1
    assert call_kwargs["tools"][0]["name"] == "post_note"


@pytest.mark.asyncio
async def test_run_checkpoint_blocked_status():
    client = _make_api_client_tool("post_note", {
        "note": "Agent appears stuck on compatibility.py import error.",
        "status": "blocked",
        "blocked_by": "dask/compatibility.py",
    })
    result = await run_ephemeral_note(
        snapshot=_make_snapshot(),
        prompt=TURN_CHECKPOINT_PROMPT,
        api_client=client,
    )
    assert result.status == "blocked"
    assert result.blocked_by == "dask/compatibility.py"


@pytest.mark.asyncio
async def test_run_checkpoint_timeout():
    client = AsyncMock()
    client.create_message = AsyncMock(side_effect=asyncio.TimeoutError())
    result = await run_ephemeral_note(
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
    result = await run_ephemeral_note(
        snapshot=_make_snapshot(),
        prompt="summarize",
        api_client=client,
    )
    assert not result.timed_out
    assert result.text == ""


# ---------------------------------------------------------------------------
# assess_pause tests (forced tool call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assess_pause_yes():
    client = _make_api_client_tool("pause_verdict", {
        "answer": "YES",
        "reason": "my task imports compatibility.py",
    })
    verdict = await assess_pause(
        snapshot=_make_snapshot(),
        broken_files=["dask/compatibility.py"],
        problem="ImportError on parse",
        api_client=client,
    )
    assert isinstance(verdict, PauseVerdict)
    assert verdict.answer == "YES"
    assert "compatibility" in verdict.reason


@pytest.mark.asyncio
async def test_assess_pause_no():
    client = _make_api_client_tool("pause_verdict", {
        "answer": "NO",
        "reason": "I only work on bag/ files",
    })
    verdict = await assess_pause(
        snapshot=_make_snapshot(),
        broken_files=["dask/compatibility.py"],
        problem="ImportError",
        api_client=client,
    )
    assert verdict.answer == "NO"
    assert "bag" in verdict.reason


@pytest.mark.asyncio
async def test_assess_pause_timeout():
    client = AsyncMock()
    client.create_message = AsyncMock(side_effect=asyncio.TimeoutError())
    verdict = await assess_pause(
        snapshot=_make_snapshot("t3"),
        broken_files=["x.py"],
        problem="broken",
        api_client=client,
        timeout_seconds=1,
    )
    assert verdict.answer == "TIMEOUT"


@pytest.mark.asyncio
async def test_assess_pause_tool_choice_enforced():
    """Verify tool_choice="any" is passed to the API so the model must call the tool."""
    client = _make_api_client_tool("pause_verdict", {
        "answer": "YES", "reason": "test",
    })
    await assess_pause(
        snapshot=_make_snapshot(),
        broken_files=["x.py"],
        problem="broken",
        api_client=client,
    )
    call_kwargs = client.create_message.call_args.kwargs
    assert call_kwargs["tool_choice"] == {"type": "any"}
    assert call_kwargs["tools"][0]["name"] == "pause_verdict"
    # answer enum is enforced in schema
    schema = call_kwargs["tools"][0]["input_schema"]
    assert schema["properties"]["answer"]["enum"] == ["YES", "NO"]


@pytest.mark.asyncio
async def test_assess_pause_conversation_includes_tool_call():
    """Verify the conversation record includes the tool_use block for resume context."""
    client = _make_api_client_tool("pause_verdict", {
        "answer": "YES", "reason": "depends on broken file",
    })
    verdict = await assess_pause(
        snapshot=_make_snapshot(),
        broken_files=["x.py"],
        problem="broken",
        api_client=client,
    )
    # Last message in conversation should be the assistant's tool_use
    assert len(verdict.conversation) >= 3  # user msgs + prompt + tool response
    last = verdict.conversation[-1]
    assert last["role"] == "assistant"


# ---------------------------------------------------------------------------
# TaskCenter.check() with EphemeralTask integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_uses_llm_when_snapshot_provided():
    tc = _tc()
    tc.graph["t1"] = Task(
        id="t1", team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    for i in range(5):
        tc.on_edit("t1", f"file{i}.py")

    client = _make_api_client_tool("post_note", {
        "note": "Edited 5 files to fix parser indentation.",
        "status": "working",
    })
    snapshot = _make_raw_messages()

    result = await tc.check("t1", snapshot=snapshot, api_client=client)
    assert result is True

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

    # Tool call with empty note — should fall back to factual
    client = _make_api_client_tool("post_note", {"note": "", "status": "working"})
    result = await tc.check("t1", snapshot=_make_raw_messages(), api_client=client)
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

    client = _make_api_client_tool("post_note", {
        "note": "Agent is working on tests, no blockers.",
        "status": "working",
    })
    await tc.check("t1", snapshot=_make_raw_messages(), api_client=client)

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
