# ruff: noqa
"""E2E live test for background task progress live-tailing.

Verifies that streaming-capable background tools can push incremental
output via ``on_progress_line`` and that ``check_background_progress``
surfaces a live tail (with ``last_n_lines`` honoured) while the task is
still running. Also guards the negative case: a non-streaming background
task must NOT leak any partial output mid-run.

No API credentials required — exercises the BackgroundTaskManager and
the real CheckBackgroundProgressTool directly with the same context
wiring as ``query.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from engine.runtime.background_tasks import BackgroundTaskManager
from tools.builtins.background.check_background_progress import (
    CheckBackgroundProgressInput,
    CheckBackgroundProgressTool,
)
from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class _StreamingInput(BaseModel):
    n_lines: int = Field(default=5)
    interval: float = Field(default=0.05)


class _StreamingTool(BaseTool):
    """Background-capable tool that emits progress lines via on_progress_line."""

    name: str = "fake_streaming"
    description: str = "Emit n_lines progress lines, sleeping interval between each."
    input_model: type[BaseModel] = _StreamingInput
    supports_background: bool = True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, _StreamingInput)
        on_line = context.metadata.get("on_progress_line")
        for i in range(arguments.n_lines):
            if on_line is not None:
                on_line(f"line {i + 1}")
            await asyncio.sleep(arguments.interval)
        return ToolResult(output="\n".join(f"line {i + 1}" for i in range(arguments.n_lines)))


@pytest.mark.asyncio
async def test_live_tail_visible_while_running() -> None:
    """While the streaming tool is mid-flight, check_background_progress
    must return the lines already emitted via on_progress_line, with
    last_n_lines honoured. After completion, the final output is available."""
    mgr = BackgroundTaskManager()
    tool = _StreamingTool()

    n_lines = 6
    interval = 0.08
    alias = mgr.next_alias()

    async def _coro() -> ToolResult:
        ctx = ToolExecutionContext(
            cwd=Path("/tmp"),
            metadata={"on_progress_line": mgr.make_progress_callback(alias)},
        )
        return await tool.execute(_StreamingInput(n_lines=n_lines, interval=interval), ctx)

    mgr.launch(alias, "fake_streaming", {}, _coro())

    # Wait long enough for ~3 lines to have been emitted, but not all 6.
    await asyncio.sleep(interval * 3 + interval / 2)

    check_tool = CheckBackgroundProgressTool()
    check_ctx = ToolExecutionContext(
        cwd=Path("/tmp"),
        metadata={"background_task_manager": mgr},
    )

    mid_result = await check_tool.execute(
        CheckBackgroundProgressInput(task_id=alias, last_n_lines=2),
        check_ctx,
    )
    assert not mid_result.is_error, mid_result.output
    assert '"status": "running"' in mid_result.output, mid_result.output
    assert '"output"' in mid_result.output, (
        f"Expected live tail in mid-flight check, got:\n{mid_result.output}"
    )
    # last_n_lines=2 → only the most recent two streamed lines should
    # appear, and earlier ones should NOT.
    assert "line 1" not in mid_result.output, mid_result.output
    assert any(f"line {i}" in mid_result.output for i in (2, 3, 4)), mid_result.output

    # Now wait for completion and re-check.
    completed = await mgr.wait_for(alias, timeout=5.0)
    assert completed is not None, "task should complete within timeout"
    assert completed.status in ("completed", "delivered")

    final_result = await check_tool.execute(
        CheckBackgroundProgressInput(task_id=alias, last_n_lines=20),
        check_ctx,
    )
    assert not final_result.is_error
    assert '"status":' in final_result.output
    assert "completed" in final_result.output or "delivered" in final_result.output
    assert f"line {n_lines}" in final_result.output


@pytest.mark.asyncio
async def test_no_streaming_means_no_output_field_while_running() -> None:
    """A background task that does NOT use on_progress_line should not
    surface any partial output until it finishes."""
    mgr = BackgroundTaskManager()

    async def _coro() -> ToolResult:
        await asyncio.sleep(0.3)
        return ToolResult(output="final only")

    alias = mgr.next_alias()
    mgr.launch(alias, "noop", {}, _coro())

    await asyncio.sleep(0.05)
    snap = mgr.get_status(alias)
    assert snap and snap[0]["status"] == "running"
    assert "output" not in snap[0], (
        f"Non-streaming task should not leak output mid-run: {snap[0]}"
    )

    await mgr.wait_for(alias, timeout=2.0)
    snap = mgr.get_status(alias)
    assert snap[0]["status"] in ("completed", "delivered")
    assert snap[0]["output"] == "final only"
