"""Unit tests for ``BackgroundTaskSupervisor._apply_terminal_status_transition``.

The single-latch invariant (plan Pre-mortem #6) says only one terminal status
wins per task, and a later, higher-precedence transition can overwrite a
lower one. Precedence: ``completed > failed > cancelled > running``.
"""

from __future__ import annotations

import asyncio

import pytest

from engine.background.task_supervisor import BackgroundTaskSupervisor, BackgroundTaskStatus
from tools import ToolResult


pytestmark = pytest.mark.asyncio


async def _launch_running_task(mgr: BackgroundTaskSupervisor, alias: str) -> None:
    """Park an asyncio task in RUNNING so we can drive transitions ourselves."""

    async def _idle() -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult(output="never")

    mgr.launch(alias, "noop", {}, _idle())


async def test_completed_overrides_cancelled() -> None:
    mgr = BackgroundTaskSupervisor()
    await _launch_running_task(mgr, "bg_a")
    tracked = mgr.get_task("bg_a")
    assert tracked is not None

    cancelled_result = ToolResult(output="Cancelled", is_error=True)
    completed_result = ToolResult(output="real result")

    # Late cancel arrives first — but a natural completion later should win.
    assert mgr._apply_terminal_status_transition(
        tracked,
        new_status=BackgroundTaskStatus.CANCELLED,
        new_result=cancelled_result,
    )
    assert tracked.status == BackgroundTaskStatus.CANCELLED
    assert tracked.result is cancelled_result

    assert mgr._apply_terminal_status_transition(
        tracked,
        new_status=BackgroundTaskStatus.COMPLETED,
        new_result=completed_result,
    )
    assert tracked.status == BackgroundTaskStatus.COMPLETED
    assert tracked.result is completed_result

    await mgr.cancel("bg_a", "cleanup")


async def test_cancelled_does_not_overwrite_completed() -> None:
    mgr = BackgroundTaskSupervisor()
    await _launch_running_task(mgr, "bg_b")
    tracked = mgr.get_task("bg_b")
    assert tracked is not None

    completed_result = ToolResult(output="real result")
    cancelled_result = ToolResult(output="Cancelled", is_error=True)

    assert mgr._apply_terminal_status_transition(
        tracked,
        new_status=BackgroundTaskStatus.COMPLETED,
        new_result=completed_result,
    )
    # Late cancel after natural completion: rejected.
    assert not mgr._apply_terminal_status_transition(
        tracked,
        new_status=BackgroundTaskStatus.CANCELLED,
        new_result=cancelled_result,
    )
    assert tracked.status == BackgroundTaskStatus.COMPLETED
    assert tracked.result is completed_result

    await mgr.cancel("bg_b", "cleanup")


async def test_failed_overrides_cancelled_but_not_completed() -> None:
    mgr = BackgroundTaskSupervisor()
    await _launch_running_task(mgr, "bg_c")
    tracked = mgr.get_task("bg_c")
    assert tracked is not None

    cancelled_result = ToolResult(output="Cancelled", is_error=True)
    failed_result = ToolResult(output="boom", is_error=True)
    completed_result = ToolResult(output="real result")

    assert mgr._apply_terminal_status_transition(
        tracked,
        new_status=BackgroundTaskStatus.CANCELLED,
        new_result=cancelled_result,
    )
    assert mgr._apply_terminal_status_transition(
        tracked,
        new_status=BackgroundTaskStatus.FAILED,
        new_result=failed_result,
    )
    assert tracked.status == BackgroundTaskStatus.FAILED
    # Completed beats failed.
    assert mgr._apply_terminal_status_transition(
        tracked,
        new_status=BackgroundTaskStatus.COMPLETED,
        new_result=completed_result,
    )
    assert tracked.status == BackgroundTaskStatus.COMPLETED

    await mgr.cancel("bg_c", "cleanup")


async def test_cancel_then_natural_completion_returns_real_result() -> None:
    """End-to-end via cancel() + done_callback (the actual race the plan calls out)."""

    mgr = BackgroundTaskSupervisor()
    # A short task that finishes before cancel propagates.
    fired = asyncio.Event()

    async def _quick() -> ToolResult:
        fired.set()
        return ToolResult(output="real result")

    alias = mgr.next_alias()
    mgr.launch(alias, "shell", {"cmd": "true"}, _quick())
    await fired.wait()
    await asyncio.sleep(0)  # let done_callback run
    # Now issue cancel: must NOT overwrite the COMPLETED status.
    await mgr.cancel(alias, "late")
    tracked = mgr.get_task(alias)
    assert tracked is not None
    assert tracked.status == BackgroundTaskStatus.COMPLETED
    assert tracked.result is not None
    assert tracked.result.output == "real result"
