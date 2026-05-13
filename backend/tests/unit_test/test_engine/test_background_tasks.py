"""Tests for BackgroundTaskManager."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from engine.background.manager import BackgroundTaskManager
from message.stream_events import BackgroundTaskStarted
from tools.background.cancel_background_task import (
    CancelBackgroundTaskInput,
    CancelBackgroundTaskTool,
)
from tools._framework.core.base import ToolExecutionContextService, ToolResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _make_tool_coro(
    output: str = "done", is_error: bool = False, delay: float = 0
) -> ToolResult:
    if delay:
        await asyncio.sleep(delay)
    return ToolResult(output=output, is_error=is_error)


def _launch(
    mgr: BackgroundTaskManager,
    *,
    task_id: str = "t1",
    tool_name: str = "test_tool",
    tool_input: dict[str, Any] | None = None,
    delay: float = 0.0,
    output: str = "ok",
    task_type: str = "tool",
):
    """Thin wrapper: creates the coro and calls mgr.launch with sensible defaults."""
    return mgr.launch(
        task_id=task_id,
        tool_name=tool_name,
        tool_input=tool_input if tool_input is not None else {},
        coro=_make_tool_coro(output=output, delay=delay),
        task_type=task_type,
    )


def _launch_subagent(
    mgr: BackgroundTaskManager,
    *,
    task_id: str = "bg_1",
    delay: float = 10.0,
    coro=None,
):
    """Launch a subagent-typed task."""
    return mgr.launch(
        task_id=task_id,
        tool_name="run_subagent",
        tool_input={"agent_name": "test_subagent"},
        coro=coro if coro is not None else _make_tool_coro(delay=delay),
        task_type="subagent",
    )


def _make_ctx(mgr: BackgroundTaskManager) -> ToolExecutionContextService:
    return ToolExecutionContextService(cwd="/tmp", services={"background_task_manager": mgr})


# ---------------------------------------------------------------------------
# 1. launch
# ---------------------------------------------------------------------------


async def test_launch_creates_task() -> None:
    mgr = BackgroundTaskManager()
    event = mgr.launch(
        task_id="t1",
        tool_name="my_tool",
        tool_input={"key": "val"},
        coro=_make_tool_coro(),
    )

    assert isinstance(event, BackgroundTaskStarted)
    assert event.task_id == "t1"
    assert event.tool_name == "my_tool"
    assert event.tool_input == {"key": "val"}
    assert mgr.has_pending()
    assert "t1" in mgr._tasks


# ---------------------------------------------------------------------------
# 2. collect_completed after task finishes
# ---------------------------------------------------------------------------


async def test_collect_completed_after_task_finishes() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr, task_id="t1", tool_name="fast_tool", output="hello")
    await asyncio.sleep(0.01)

    completed = mgr.collect_completed()
    assert len(completed) == 1
    assert completed[0].status == "delivered"
    assert completed[0].result is not None
    assert completed[0].result.output == "hello"
    assert completed[0].result.is_error is False


# ---------------------------------------------------------------------------
# 3. collect_completed only once
# ---------------------------------------------------------------------------


async def test_collect_completed_only_once() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr)
    await asyncio.sleep(0.01)

    first = mgr.collect_completed()
    assert len(first) == 1

    second = mgr.collect_completed()
    assert len(second) == 0


# ---------------------------------------------------------------------------
# 4. has_pending
# ---------------------------------------------------------------------------


async def test_has_pending() -> None:
    mgr = BackgroundTaskManager()
    assert mgr.has_pending() is False

    _launch(mgr, delay=1.0)
    assert mgr.has_pending() is True

    # Cancel so we don't leak the slow task.
    await mgr.cancel_all()
    assert mgr.has_pending() is False


# ---------------------------------------------------------------------------
# 8. cancel running task
# ---------------------------------------------------------------------------


async def test_cancel_running_task() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr, tool_name="slow", delay=10)

    ok = await mgr.cancel("t1", "test reason")
    assert ok is True

    tracked = mgr._tasks["t1"]
    assert tracked.status == "cancelled"
    assert tracked.result is not None
    assert tracked.result.output == "Cancelled: test reason"
    assert tracked.result.is_error is True


# ---------------------------------------------------------------------------
# 9. cancel nonexistent task
# ---------------------------------------------------------------------------


async def test_cancel_nonexistent_task() -> None:
    mgr = BackgroundTaskManager()
    assert await mgr.cancel("nonexistent_id") is False


# ---------------------------------------------------------------------------
# 10. cancel all
# ---------------------------------------------------------------------------


async def test_cancel_all() -> None:
    mgr = BackgroundTaskManager()
    for i in range(3):
        _launch(mgr, task_id=f"t{i}", tool_name=f"tool{i}", delay=10)

    await mgr.cancel_all()

    for i in range(3):
        assert mgr._tasks[f"t{i}"].status == "cancelled"
    assert mgr.has_pending() is False


async def test_cancel_all_marks_subagent_cancelled_without_asyncio_cancel() -> None:
    mgr = BackgroundTaskManager()
    cancelled = asyncio.Event()

    async def _subagent_coro() -> ToolResult:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ToolResult(output="done")

    _launch_subagent(mgr, task_id="bg_1", coro=_subagent_coro())

    await mgr.cancel_all()
    await asyncio.sleep(0)

    tracked = mgr._tasks["bg_1"]
    assert tracked.status == "cancelled"
    assert tracked.result is not None
    assert tracked.result.output == "Cancelled"
    assert cancelled.is_set() is False
    assert tracked.asyncio_task.cancelled() is False

    tracked.asyncio_task.cancel()
    with suppress(asyncio.CancelledError):
        await tracked.asyncio_task


async def test_cancel_subagent_requests_early_stop_and_preserves_result() -> None:
    mgr = BackgroundTaskManager()
    cancelled = asyncio.Event()

    async def _subagent_coro() -> ToolResult:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            return ToolResult(output="partial summary")
        return ToolResult(output="done")

    _launch_subagent(mgr, task_id="bg_early", coro=_subagent_coro())

    ok = await mgr.cancel("bg_early", "enough evidence")
    assert ok is True
    await asyncio.sleep(0)

    tracked = mgr._tasks["bg_early"]
    assert cancelled.is_set() is True
    assert tracked.status == "completed"
    assert tracked.stop_mode == "early_stop"
    assert tracked.completion_mode == "early_stopped"
    assert tracked.result is not None
    assert tracked.result.output == "partial summary"


# ---------------------------------------------------------------------------
# 11. task that raises exception
# ---------------------------------------------------------------------------


async def _raise_coro() -> ToolResult:
    raise ValueError("something broke")


async def test_task_that_raises_exception() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="bad_tool",
        tool_input={},
        coro=_raise_coro(),
    )
    await asyncio.sleep(0.01)

    completed = mgr.collect_completed()
    assert len(completed) == 1

    task = completed[0]
    # After collect, status is "delivered" (was "failed").
    assert task.status == "delivered"
    assert task.result is not None
    assert task.result.is_error is True
    assert "something broke" in task.result.output


# ---------------------------------------------------------------------------
# 16. progress_lines populated
# ---------------------------------------------------------------------------


async def test_progress_lines_populated() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr, output="line1\nline2\nline3")
    await asyncio.sleep(0.01)

    tracked = mgr._tasks["t1"]
    assert tracked.progress_lines == ["line1", "line2", "line3"]


# ---------------------------------------------------------------------------
# 17. multiple concurrent tasks
# ---------------------------------------------------------------------------


async def test_multiple_concurrent_tasks() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr, task_id="fast", tool_name="fast", output="fast_done", delay=0.01)
    _launch(mgr, task_id="medium", tool_name="medium", output="medium_done", delay=0.05)
    _launch(mgr, task_id="slow", tool_name="slow", output="slow_done", delay=0.1)

    await asyncio.sleep(0.15)
    completed = mgr.collect_completed()
    assert {t.task_id for t in completed} == {"fast", "medium", "slow"}


async def test_cancel_marks_task_cancelled() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr, tool_name="slow", delay=10)

    ok = await mgr.cancel("t1", "no kill cb")
    assert ok is True
    assert mgr._tasks["t1"].status == "cancelled"


async def test_cancel_all_cancels_python_task() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr, tool_name="slow", delay=10)
    task = mgr._tasks["t1"].asyncio_task

    await mgr.cancel_all()
    await asyncio.sleep(0)

    assert task.cancelled() or task.done()


# ---------------------------------------------------------------------------
# 27. done_callback does not overwrite cancelled status
# ---------------------------------------------------------------------------


async def test_done_callback_skips_cancelled_status() -> None:
    """If cancel() ran first, the asyncio task completing later must not
    overwrite the cancelled state with completed."""
    mgr = BackgroundTaskManager()
    _launch(mgr, output="late-completion", delay=0.05)
    await mgr.cancel("t1", "early")
    await asyncio.sleep(0.10)

    assert mgr._tasks["t1"].status == "cancelled"
    assert "early" in mgr._tasks["t1"].result.output
    assert "late-completion" not in mgr._tasks["t1"].result.output


async def test_done_callback_handles_asyncio_cancel_without_loop_error() -> None:
    """Cancelling a pure-Python background task must not trigger the loop's
    exception handler from the done callback."""
    mgr = BackgroundTaskManager()
    loop = asyncio.get_running_loop()
    observed: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()

    def _handler(loop, context):  # type: ignore[no-untyped-def]
        observed.append(context)

    loop.set_exception_handler(_handler)
    try:
        _launch(mgr, task_id="t_cancel", tool_name="t", delay=10)
        await mgr.cancel("t_cancel", "stop")
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert observed == []
    assert mgr._tasks["t_cancel"].status == "cancelled"
    assert "stop" in mgr._tasks["t_cancel"].result.output


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 31. cancel_background_task tool rejects task_id="all"
# ---------------------------------------------------------------------------


async def test_cancel_tool_rejects_all_sentinel() -> None:
    mgr = BackgroundTaskManager()
    _launch(mgr, task_id="bg_1", tool_name="t", delay=10)

    tool = CancelBackgroundTaskTool()
    args = CancelBackgroundTaskInput(task_id="all")
    result = await tool.execute(args, _make_ctx(mgr))
    assert result.is_error is True
    assert "does not support" in result.output
    assert mgr._tasks["bg_1"].status == "running"

    await mgr.cancel("bg_1")
