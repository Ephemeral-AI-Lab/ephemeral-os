"""Tests for posthook submission tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tools.context.toolkit import PostNoteTool
from tools.core.base import ToolExecutionContext
from tools.posthook.toolkit import (
    AddTasksTool,
    CancelAndRedraftTool,
    DeclareBlockerTool,
    PosthookTools,
    RequestReplanTool,
)


class _FakeTaskCenter:
    def __init__(self):
        self.notes = self  # production code calls tc.notes.post(note)
        self.posted = []

    async def post(self, note):
        self.posted.append(note)


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def test_post_note_accepts_content():
    """PostNoteTool accepts content and posts note."""
    ctx = _ctx({"task_center": _FakeTaskCenter()})

    result = asyncio.run(PostNoteTool().execute(
        PostNoteTool.input_model(content="patched compatibility handling"),
        ctx,
    ))

    assert not result.is_error
    assert "posted" in result.output.lower()


def test_post_note_rejects_scout_scope_repair_for_missing_target():
    ctx = _ctx({
        "task_center": _FakeTaskCenter(),
        "agent_name": "scout",
        "write_scope": ["dvc/repo/plots"],
    })

    result = asyncio.run(PostNoteTool().execute(
        PostNoteTool.input_model(
            content=(
                "`dvc/repo/plots` does not exist. "
                "The intended path is `dvc/repo/plot`."
            )
        ),
        ctx,
    ))

    assert result.is_error
    assert "keep missing targets missing" in result.output


def test_post_note_sanitizes_scout_gap_evidence_paths():
    tc = _FakeTaskCenter()
    ctx = _ctx({
        "task_center": tc,
        "agent_name": "scout",
        "write_scope": ["dvc/command/plots.py"],
    })

    result = asyncio.run(PostNoteTool().execute(
        PostNoteTool.input_model(
            content=(
                "`dvc/command/plots.py` does not exist. "
                "Evidence from workspace structure: `dvc/command/plot.py` exists nearby."
            )
        ),
        ctx,
    ))

    assert not result.is_error
    assert tc.posted
    assert "`dvc/command/plot.py`" not in tc.posted[0].content
    assert "dvc/command/plot.py exists nearby" in tc.posted[0].content


def test_post_note_rejects_empty_content():
    """PostNoteTool requires non-empty content."""
    with pytest.raises(Exception):
        PostNoteTool.input_model(content="")


def test_add_tasks_returns_success():
    ctx = _ctx({})

    result = asyncio.run(AddTasksTool().execute(
        AddTasksTool.input_model(
            add_tasks=[{"id": "fix-1", "task": "fix owner", "agent": "developer"}],
            cancel_ids=[],
        ),
        ctx,
    ))

    assert not result.is_error
    assert "1 new tasks" in result.output or "Replan accepted" in result.output


def test_declare_blocker_returns_success():
    ctx = _ctx({})

    result = asyncio.run(DeclareBlockerTool().execute(
        DeclareBlockerTool.input_model(
            root_cause_paths=["pkg/shared.py"],
            reason="shared import crash",
            suggestion="restore exported helper",
        ),
        ctx,
    ))

    assert not result.is_error
    assert "Blocker declared" in result.output


def test_cancel_and_redraft_returns_success():
    ctx = _ctx({})

    result = asyncio.run(CancelAndRedraftTool().execute(
        CancelAndRedraftTool.input_model(
            add_tasks=[{"id": "fix-2", "task": "rewrite lane", "agent": "developer"}],
            cancel_ids=["old-1"],
        ),
        ctx,
    ))

    assert not result.is_error
    assert "Replan accepted" in result.output or "Cancelled" in result.output


def test_posthook_tools_resolver_role_gets_terminal_submission_tools():
    ctx = _ctx({"role": "resolver"})

    toolkit = PosthookTools.from_context(ctx)

    assert [tool.name for tool in toolkit.list_tools()] == [
        PostNoteTool.name,
        RequestReplanTool.name,
    ]
