"""Unit tests for TaskCenter active-mode (check / auto-note generation)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from team.models import BudgetConfig, BudgetState, Note, Task, TaskStatus
from team.task_center import TaskCenter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _seed_task(tc: TaskCenter, task_id: str = "t1") -> Task:
    t = Task(
        id=task_id, team_run_id="run-1", agent_name="developer",
        status=TaskStatus.RUNNING, task="fix parser",
    )
    tc.graph[task_id] = t
    return t


# ---------------------------------------------------------------------------
# TaskCenter.check() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_falls_back_to_factual_without_api_client():
    tc = _tc()
    _seed_task(tc)
    for i in range(5):
        tc.on_edit("t1", f"file{i}.py")

    result = await tc.check("t1")
    assert result is True

    notes = await tc.read()
    assert len(notes) == 1
    assert "Auto-checkpoint" in notes[0].content
    assert "5 edits" in notes[0].content


@pytest.mark.asyncio
async def test_check_uses_llm_when_snapshot_provided():
    tc = _tc()
    _seed_task(tc)
    for i in range(5):
        tc.on_edit("t1", f"file{i}.py")

    from external_trigger.tc_note import NoteSummary

    mock_result = NoteSummary(
        task_id="t1", trigger="edit",
        note_summary="Edited 5 files to fix parser indentation.",
        turns_used=1,
    )

    with patch("external_trigger.tc_note.run_checkpoint_note", new_callable=AsyncMock, return_value=mock_result):
        snapshot = [
            {"role": "user", "content": "Fix the bug in parser.py"},
            {"role": "assistant", "content": "I'll look at parser.py now."},
        ]
        result = await tc.check("t1", snapshot=snapshot, api_client=AsyncMock())

    assert result is True
    notes = await tc.read()
    assert len(notes) == 1
    assert notes[0].content == "Edited 5 files to fix parser indentation."
    assert notes[0].agent_name == "developer (auto)"


@pytest.mark.asyncio
async def test_check_falls_back_on_empty_llm_response():
    tc = _tc()
    _seed_task(tc)
    for _ in range(15):
        tc.tick("t1")

    from external_trigger.tc_note import NoteSummary

    mock_result = NoteSummary(task_id="t1", trigger="turn", note_summary="", turns_used=1)

    with patch("external_trigger.tc_note.run_checkpoint_note", new_callable=AsyncMock, return_value=mock_result):
        result = await tc.check("t1", snapshot=[], api_client=AsyncMock())

    assert result is True
    notes = await tc.read()
    assert "Auto-checkpoint" in notes[0].content
    assert "15 turns" in notes[0].content


@pytest.mark.asyncio
async def test_check_no_threshold_crossed():
    tc = _tc()
    _seed_task(tc)
    tc.on_edit("t1", "a.py")  # Only 1 edit, threshold is 5

    result = await tc.check("t1")
    assert result is False
    assert len(await tc.read()) == 0


@pytest.mark.asyncio
async def test_check_turn_threshold_is_15():
    """Turn threshold is 15 (discounting edit turns)."""
    tc = _tc()
    _seed_task(tc)
    for _ in range(14):
        tc.tick("t1")
    assert tc.should_checkpoint("t1") is None

    tc.tick("t1")
    assert tc.should_checkpoint("t1") == "turn"


@pytest.mark.asyncio
async def test_check_edit_resets_turn_counter():
    tc = _tc()
    _seed_task(tc)
    for _ in range(10):
        tc.tick("t1")
    tc.on_edit("t1", "a.py")
    assert tc._get_counters("t1")["turns"] == 0


@pytest.mark.asyncio
async def test_check_resets_counters_after_note():
    tc = _tc()
    _seed_task(tc)
    for i in range(5):
        tc.on_edit("t1", f"file{i}.py")

    await tc.check("t1")

    # Counters should be reset after note posted
    assert tc.should_checkpoint("t1") is None
