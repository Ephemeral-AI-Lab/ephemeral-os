"""Tests for BackgroundTaskManager."""

from __future__ import annotations

import asyncio
import time

import pytest

from engine.runtime.background_tasks import BackgroundTaskManager, TrackedBackgroundTask
from message.stream_events import BackgroundTaskStarted
from tools.core.base import ToolResult


async def _make_tool_coro(
    output: str = "done", is_error: bool = False, delay: float = 0
) -> ToolResult:
    if delay:
        await asyncio.sleep(delay)
    return ToolResult(output=output, is_error=is_error)


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
    mgr.launch(
        task_id="t1",
        tool_name="fast_tool",
        tool_input={},
        coro=_make_tool_coro(output="hello"),
    )
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
    mgr.launch(
        task_id="t1",
        tool_name="tool",
        tool_input={},
        coro=_make_tool_coro(),
    )
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

    mgr.launch(
        task_id="t1",
        tool_name="slow",
        tool_input={},
        coro=_make_tool_coro(delay=1.0),
    )
    assert mgr.has_pending() is True

    # Cancel so we don't leak the slow task.
    mgr.cancel_all()
    assert mgr.has_pending() is False


# ---------------------------------------------------------------------------
# 5. wait_any returns on completion
# ---------------------------------------------------------------------------


async def test_wait_any_returns_on_completion() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="tool",
        tool_input={},
        coro=_make_tool_coro(output="waited", delay=0.1),
    )

    start = time.monotonic()
    result = await mgr.wait_any(timeout=5)
    elapsed = time.monotonic() - start

    assert result is not None
    assert result.task_id == "t1"
    assert result.result is not None
    assert result.result.output == "waited"
    # Should complete in roughly 0.1s, not 5s.
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# 6. wait_any timeout
# ---------------------------------------------------------------------------


async def test_wait_any_timeout() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="very_slow",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )

    result = await mgr.wait_any(timeout=0.1)
    assert result is None

    mgr.cancel_all()


# ---------------------------------------------------------------------------
# 7. wait_any no pending
# ---------------------------------------------------------------------------


async def test_wait_any_no_pending() -> None:
    mgr = BackgroundTaskManager()
    result = await mgr.wait_any()
    assert result is None


# ---------------------------------------------------------------------------
# 8. cancel running task
# ---------------------------------------------------------------------------


async def test_cancel_running_task() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="slow",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )

    ok = mgr.cancel("t1", "test reason")
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
    assert mgr.cancel("nonexistent_id") is False


# ---------------------------------------------------------------------------
# 10. cancel all
# ---------------------------------------------------------------------------


async def test_cancel_all() -> None:
    mgr = BackgroundTaskManager()
    for i in range(3):
        mgr.launch(
            task_id=f"t{i}",
            tool_name=f"tool{i}",
            tool_input={},
            coro=_make_tool_coro(delay=10),
        )

    mgr.cancel_all()

    for i in range(3):
        assert mgr._tasks[f"t{i}"].status == "cancelled"
    assert mgr.has_pending() is False


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
# 12. compact_status
# ---------------------------------------------------------------------------


async def test_compact_status() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="alpha",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )
    mgr.launch(
        task_id="t2",
        tool_name="beta",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )

    status = mgr.compact_status()
    assert "alpha" in status
    assert "beta" in status
    assert "t1" in status
    assert "t2" in status
    assert "running" in status

    mgr.cancel_all()


# ---------------------------------------------------------------------------
# 13. get_status all
# ---------------------------------------------------------------------------


async def test_get_status_all() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="tool_a",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )
    mgr.launch(
        task_id="t2",
        tool_name="tool_b",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )

    statuses = mgr.get_status()
    assert len(statuses) == 2

    ids = {s["task_id"] for s in statuses}
    assert ids == {"t1", "t2"}

    for s in statuses:
        assert "tool_name" in s
        assert "status" in s
        assert "elapsed_seconds" in s

    mgr.cancel_all()


# ---------------------------------------------------------------------------
# 14. get_status by id
# ---------------------------------------------------------------------------


async def test_get_status_by_id() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="tool_a",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )
    mgr.launch(
        task_id="t2",
        tool_name="tool_b",
        tool_input={},
        coro=_make_tool_coro(delay=10),
    )

    statuses = mgr.get_status(task_id="t1")
    assert len(statuses) == 1
    assert statuses[0]["task_id"] == "t1"

    mgr.cancel_all()


# ---------------------------------------------------------------------------
# 15. get_status truncates output
# ---------------------------------------------------------------------------


async def test_get_status_truncates_output() -> None:
    mgr = BackgroundTaskManager()
    long_output = "x" * 5000
    mgr.launch(
        task_id="t1",
        tool_name="tool",
        tool_input={},
        coro=_make_tool_coro(output=long_output),
    )
    await asyncio.sleep(0.01)

    statuses = mgr.get_status()
    assert len(statuses) == 1
    output = statuses[0]["output"]
    # The implementation truncates to 2000 chars + "... (truncated)".
    assert len(output) <= 2020
    assert output.endswith("... (truncated)")


# ---------------------------------------------------------------------------
# 16. progress_lines populated
# ---------------------------------------------------------------------------


async def test_progress_lines_populated() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="t1",
        tool_name="tool",
        tool_input={},
        coro=_make_tool_coro(output="line1\nline2\nline3"),
    )
    await asyncio.sleep(0.01)

    tracked = mgr._tasks["t1"]
    assert tracked.progress_lines == ["line1", "line2", "line3"]


# ---------------------------------------------------------------------------
# 17. multiple concurrent tasks
# ---------------------------------------------------------------------------


async def test_multiple_concurrent_tasks() -> None:
    mgr = BackgroundTaskManager()
    mgr.launch(
        task_id="fast",
        tool_name="fast",
        tool_input={},
        coro=_make_tool_coro(output="fast_done", delay=0.01),
    )
    mgr.launch(
        task_id="medium",
        tool_name="medium",
        tool_input={},
        coro=_make_tool_coro(output="medium_done", delay=0.05),
    )
    mgr.launch(
        task_id="slow",
        tool_name="slow",
        tool_input={},
        coro=_make_tool_coro(output="slow_done", delay=0.1),
    )

    # wait_any should return the fastest first.
    first = await mgr.wait_any(timeout=5)
    assert first is not None
    assert first.task_id == "fast"

    # Wait for the rest.
    await asyncio.sleep(0.15)
    remaining = mgr.collect_completed()
    remaining_ids = {t.task_id for t in remaining}
    assert "medium" in remaining_ids
    assert "slow" in remaining_ids
