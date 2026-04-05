"""Unit tests for check_background_progress and cancel_background_task tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from engine.background_tasks import BackgroundTaskManager
from tools.base import ToolExecutionContext, ToolResult
from tools.builtins.cancel_background_task import CancelBackgroundTaskInput, CancelBackgroundTaskTool
from tools.builtins.check_background_progress import CheckBackgroundProgressInput, CheckBackgroundProgressTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(manager: BackgroundTaskManager | None = None) -> ToolExecutionContext:
    metadata: dict = {}
    if manager is not None:
        metadata["background_task_manager"] = manager
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata)


async def _slow_coro() -> ToolResult:
    await asyncio.sleep(10)
    return ToolResult(output="done")


async def _fast_coro(output: str = "finished") -> ToolResult:
    return ToolResult(output=output)


# ---------------------------------------------------------------------------
# CheckBackgroundProgressTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_no_manager():
    """Context has no background_task_manager -> returns informational message."""
    tool = CheckBackgroundProgressTool()
    result = await tool.execute(CheckBackgroundProgressInput(), _make_context())
    assert result.is_error is False
    assert "No background tasks are running." in result.output


@pytest.mark.asyncio
async def test_check_no_tasks():
    """Manager exists but has no tasks -> returns 'No background tasks.'"""
    manager = BackgroundTaskManager()
    tool = CheckBackgroundProgressTool()
    result = await tool.execute(CheckBackgroundProgressInput(), _make_context(manager))
    assert result.is_error is False
    assert "No background tasks." in result.output


@pytest.mark.asyncio
async def test_check_returns_all_tasks():
    """Launch 2 tasks, call tool without task_id -> both tasks appear in JSON output."""
    manager = BackgroundTaskManager()
    manager.launch("task-1", "tool_a", {}, _slow_coro())
    manager.launch("task-2", "tool_b", {}, _slow_coro())

    tool = CheckBackgroundProgressTool()
    result = await tool.execute(CheckBackgroundProgressInput(), _make_context(manager))

    assert result.is_error is False
    data = json.loads(result.output)
    ids = {entry["task_id"] for entry in data}
    assert ids == {"task-1", "task-2"}

    # Clean up
    manager.cancel_all()


@pytest.mark.asyncio
async def test_check_filter_by_task_id():
    """Launch 2 tasks, call with specific task_id -> only that task is returned."""
    manager = BackgroundTaskManager()
    manager.launch("task-a", "tool_x", {}, _slow_coro())
    manager.launch("task-b", "tool_y", {}, _slow_coro())

    tool = CheckBackgroundProgressTool()
    result = await tool.execute(
        CheckBackgroundProgressInput(task_id="task-a"),
        _make_context(manager),
    )

    assert result.is_error is False
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["task_id"] == "task-a"

    # Clean up
    manager.cancel_all()


@pytest.mark.asyncio
async def test_check_unknown_task_id():
    """Call with a nonexistent task_id -> returns an error result."""
    manager = BackgroundTaskManager()
    tool = CheckBackgroundProgressTool()
    result = await tool.execute(
        CheckBackgroundProgressInput(task_id="no-such-task"),
        _make_context(manager),
    )
    assert result.is_error is True
    assert "no-such-task" in result.output


@pytest.mark.asyncio
async def test_check_is_read_only():
    """CheckBackgroundProgressTool.is_read_only() must return True."""
    tool = CheckBackgroundProgressTool()
    assert tool.is_read_only(CheckBackgroundProgressInput()) is True


# ---------------------------------------------------------------------------
# CancelBackgroundTaskTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_no_manager():
    """No manager in context -> returns error."""
    tool = CancelBackgroundTaskTool()
    result = await tool.execute(
        CancelBackgroundTaskInput(task_id="any-task"),
        _make_context(),
    )
    assert result.is_error is True
    assert "No background task manager" in result.output


@pytest.mark.asyncio
async def test_cancel_running_task():
    """Launch a slow task, cancel it -> returns success message containing task id."""
    manager = BackgroundTaskManager()
    manager.launch("slow-task", "slow_tool", {}, _slow_coro())

    tool = CancelBackgroundTaskTool()
    result = await tool.execute(
        CancelBackgroundTaskInput(task_id="slow-task"),
        _make_context(manager),
    )

    assert result.is_error is False
    assert "slow-task" in result.output
    assert "cancelled" in result.output.lower()


@pytest.mark.asyncio
async def test_cancel_with_reason():
    """Cancel with a reason -> reason text appears in the output."""
    manager = BackgroundTaskManager()
    manager.launch("task-with-reason", "some_tool", {}, _slow_coro())

    tool = CancelBackgroundTaskTool()
    result = await tool.execute(
        CancelBackgroundTaskInput(task_id="task-with-reason", reason="no longer needed"),
        _make_context(manager),
    )

    assert result.is_error is False
    assert "no longer needed" in result.output


@pytest.mark.asyncio
async def test_cancel_nonexistent():
    """Cancel an unknown task id -> returns error."""
    manager = BackgroundTaskManager()
    tool = CancelBackgroundTaskTool()
    result = await tool.execute(
        CancelBackgroundTaskInput(task_id="ghost-task"),
        _make_context(manager),
    )
    assert result.is_error is True
    assert "ghost-task" in result.output
