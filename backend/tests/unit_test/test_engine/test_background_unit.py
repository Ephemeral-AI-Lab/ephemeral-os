"""Unit tests for background-task tool plumbing.

Covers, all offline (no sandbox, no LLM):

    1. `WaitBackgroundTasks` / `CheckBackgroundTaskResult` /
       `CancelBackgroundTask` schemas and ``execute`` branches that don't
       require a running loop to assert.
    2. `BackgroundTaskSupervisor` extras and live-progress tail behaviour.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pathlib import Path
from pydantic import ValidationError

from tools.background._lib.task_output import (
    background_task_display_status,
    build_background_snapshot_metadata,
    render_background_snapshot,
    render_background_tool_call,
)
from tools.background.wait_background_tasks import (
    WaitBackgroundTasksInput,
    WaitBackgroundTasksTool,
)
from tools.background.check_background_task_result import (
    CheckBackgroundTaskResultInput,
    CheckBackgroundTaskResultTool,
)
from tools.background.cancel_background_task import (
    CancelBackgroundTaskInput,
    CancelBackgroundTaskTool,
)
from tools._framework.core.base import ToolExecutionContextService, ToolResult
from engine.background.task_supervisor import BackgroundTaskSupervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(manager: BackgroundTaskSupervisor | None) -> ToolExecutionContextService:
    metadata = {"background_task_manager": manager} if manager else {}
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TestSchemas:
    @pytest.mark.parametrize("bad_timeout", [0, 0.5, 301, 1000])
    def test_wait_rejects_out_of_range_timeout(self, bad_timeout: float) -> None:
        with pytest.raises(ValidationError):
            WaitBackgroundTasksInput(timeout=bad_timeout)

    def test_wait_accepts_default_timeout(self) -> None:
        args = WaitBackgroundTasksInput()
        assert args.timeout == 30

    def test_check_requires_task_id(self) -> None:
        with pytest.raises(ValidationError):
            CheckBackgroundTaskResultInput()  # type: ignore[call-arg]

    def test_cancel_requires_task_id(self) -> None:
        with pytest.raises(ValidationError):
            CancelBackgroundTaskInput()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# render_background_tool_call / background_task_display_status
# ---------------------------------------------------------------------------


class TestCommonHelpers:
    def test_render_background_tool_call_joins_values(self) -> None:
        out = render_background_tool_call(
            "run_subagent",
            {"agent_name": "explorer", "prompt": "hi"},
        )
        assert out == "run_subagent(explorer, hi)"

    def test_render_background_tool_call_no_args(self) -> None:
        assert render_background_tool_call("noop", {}) == "noop()"

    @pytest.mark.parametrize("raw,expected", [
        ("running", "running"),
        ("completed", "finished"),
        ("delivered", "finished"),
        ("failed", "failed"),
        ("cancelled", "failed"),
    ])
    def test_background_task_display_status(self, raw: str, expected: str) -> None:
        assert background_task_display_status(raw) == expected


# ---------------------------------------------------------------------------
# WaitBackgroundTasksTool branches
# ---------------------------------------------------------------------------


class TestWaitBackgroundTasksExecute:
    async def test_no_manager_returns_error(self) -> None:
        tool = WaitBackgroundTasksTool()
        result = await tool.execute(WaitBackgroundTasksInput(timeout=1), _ctx(None))
        assert result.is_error

    async def test_no_tasks_ever(self) -> None:
        tool = WaitBackgroundTasksTool()
        mgr = BackgroundTaskSupervisor()
        result = await tool.execute(WaitBackgroundTasksInput(timeout=1), _ctx(mgr))
        assert not result.is_error
        assert "[NO TASKS]" in result.output
        assert result.metadata["background_snapshot"]["kind"] == "wait_no_tasks"

    async def test_completed_tasks_appear_in_snapshot(self) -> None:
        tool = WaitBackgroundTasksTool()
        mgr = BackgroundTaskSupervisor()

        async def fast(output: str) -> ToolResult:
            return ToolResult(output=output)

        mgr.launch("bg_1", "noop", {"q": "ping"}, fast("hi"))
        await asyncio.sleep(0.01)

        result = await tool.execute(WaitBackgroundTasksInput(timeout=1), _ctx(mgr))
        assert "[COMPLETED]" in result.output
        snap = result.metadata["background_snapshot"]
        assert snap["kind"] == "wait_completed"
        assert snap["statuses"] == [
            {"task_id": "bg_1", "status": "finished", "tool_command": "noop(ping)"},
        ]

    async def test_timeout_returns_timed_out(self) -> None:
        tool = WaitBackgroundTasksTool()
        mgr = BackgroundTaskSupervisor()

        async def slow() -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(output="done")

        mgr.launch("bg_run", "noop", {}, slow())
        try:
            result = await tool.execute(WaitBackgroundTasksInput(timeout=1), _ctx(mgr))
            assert "[TIMED_OUT" in result.output
            assert result.metadata["background_snapshot"]["kind"] == "wait_timed_out"
        finally:
            await mgr.cancel("bg_run")


# ---------------------------------------------------------------------------
# CheckBackgroundTaskResultTool branches
# ---------------------------------------------------------------------------


class TestCheckBackgroundTaskResultExecute:
    async def test_no_manager_returns_error(self) -> None:
        tool = CheckBackgroundTaskResultTool()
        result = await tool.execute(CheckBackgroundTaskResultInput(task_id="bg_1"), _ctx(None))
        assert result.is_error

    async def test_unknown_task_id(self) -> None:
        tool = CheckBackgroundTaskResultTool()
        mgr = BackgroundTaskSupervisor()
        result = await tool.execute(CheckBackgroundTaskResultInput(task_id="bg_x"), _ctx(mgr))
        assert result.is_error
        assert "bg_x" in result.output

    async def test_running_generic_tool_returns_progress_lines(self) -> None:
        tool = CheckBackgroundTaskResultTool()
        mgr = BackgroundTaskSupervisor()

        async def slow() -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(output="done")

        mgr.launch("bg_1", "shell", {"cmd": "ls"}, slow())
        try:
            mgr.append_progress("bg_1", "line-a")
            result = await tool.execute(
                CheckBackgroundTaskResultInput(task_id="bg_1"), _ctx(mgr)
            )
            payload = json.loads(result.output)
            assert payload["id"] == "bg_1"
            assert payload["status"] == "running"
            assert payload["tool_command"] == "shell(ls)"
            assert "line-a" in payload["result"]
        finally:
            await mgr.cancel("bg_1")

    async def test_finished_generic_tool_returns_full_output(self) -> None:
        tool = CheckBackgroundTaskResultTool()
        mgr = BackgroundTaskSupervisor()

        async def fast() -> ToolResult:
            return ToolResult(output="x" * 5000)

        mgr.launch("bg_1", "shell", {"cmd": "ls"}, fast())
        await asyncio.sleep(0.01)

        result = await tool.execute(
            CheckBackgroundTaskResultInput(task_id="bg_1"), _ctx(mgr)
        )
        payload = json.loads(result.output)
        assert payload["status"] == "finished"
        # No truncation for shell.
        assert payload["result"] == "x" * 5000

    async def test_subagent_is_rejected_by_generic_check_tool(self) -> None:
        tool = CheckBackgroundTaskResultTool()
        mgr = BackgroundTaskSupervisor()

        async def sub() -> ToolResult:
            return ToolResult(
                output="my findings",
                metadata={"subagent_terminal_called": True},
            )

        mgr.launch("subagent_1", "run_subagent", {"agent_name": "x", "prompt": "p"}, sub(),
                   task_type="subagent")
        await asyncio.sleep(0.01)

        result = await tool.execute(
            CheckBackgroundTaskResultInput(task_id="subagent_1"), _ctx(mgr)
        )
        assert result.is_error is True
        assert "check_subagent_progress" in result.output


# ---------------------------------------------------------------------------
# Snapshot rendering helpers
# ---------------------------------------------------------------------------


class TestBackgroundSnapshotHelpers:
    def test_progress_passthrough_for_provider_history(self) -> None:
        statuses = [{"task_id": "bg_1", "status": "running", "output": "hello"}]
        output = render_background_snapshot("progress", statuses)
        metadata = build_background_snapshot_metadata("progress", "all", statuses)
        assert json.loads(output) == statuses
        assert metadata["background_snapshot"]["kind"] == "progress"

    def test_wait_completed_render(self) -> None:
        statuses = [{"task_id": "bg_1", "status": "finished", "tool_command": "noop()"}]
        output = render_background_snapshot("wait_completed", statuses)
        assert output.startswith("[COMPLETED]\n[")
        assert "Do not call wait_background_tasks again" in output

    def test_wait_timed_out_render(self) -> None:
        statuses = [{"task_id": "bg_1", "status": "running", "tool_command": "noop()"}]
        output = render_background_snapshot("wait_timed_out", statuses, elapsed_seconds=2.5)
        assert "[TIMED_OUT after 2.5s]" in output
        assert "wait_background_tasks" in output
        assert "cancel_background_task" in output

    def test_wait_no_tasks_render(self) -> None:
        output = render_background_snapshot("wait_no_tasks", [])
        assert output.startswith("[NO TASKS]")


# ---------------------------------------------------------------------------
# CancelBackgroundTaskTool branches
# ---------------------------------------------------------------------------


class TestCancelBackgroundTaskExecute:
    async def test_no_manager_returns_error(self) -> None:
        tool = CancelBackgroundTaskTool()
        result = await tool.execute(CancelBackgroundTaskInput(task_id="bg_1"), _ctx(None))
        assert result.is_error

    async def test_rejects_all_sentinel(self) -> None:
        tool = CancelBackgroundTaskTool()
        mgr = BackgroundTaskSupervisor()
        result = await tool.execute(CancelBackgroundTaskInput(task_id="all"), _ctx(mgr))
        assert result.is_error
        assert "does not support" in result.output

    async def test_unknown_task_id_returns_error(self) -> None:
        tool = CancelBackgroundTaskTool()
        mgr = BackgroundTaskSupervisor()
        result = await tool.execute(CancelBackgroundTaskInput(task_id="bg_missing"), _ctx(mgr))
        assert result.is_error
        assert "bg_missing" in result.output

    async def test_subagent_is_rejected_by_generic_cancel_tool(self) -> None:
        tool = CancelBackgroundTaskTool()
        mgr = BackgroundTaskSupervisor()

        async def _subagent() -> ToolResult:
            await asyncio.sleep(10)
            return ToolResult(output="done")

        mgr.launch(
            task_id="subagent_1",
            tool_name="run_subagent",
            tool_input={"agent_name": "test_subagent"},
            coro=_subagent(),
            task_type="subagent",
        )
        result = await tool.execute(
            CancelBackgroundTaskInput(task_id="subagent_1"), _ctx(mgr)
        )
        assert result.is_error is True
        assert "cancel_subagent" in result.output
        await mgr.cancel("subagent_1")


# ---------------------------------------------------------------------------
# BackgroundTaskSupervisor — internal API not covered by test_background_tasks.py
# ---------------------------------------------------------------------------


class TestBackgroundTaskSupervisorExtras:
    async def test_next_alias_is_monotonic(self) -> None:
        mgr = BackgroundTaskSupervisor()
        ids = [mgr.next_alias() for _ in range(3)]
        assert ids == ["bg_1", "bg_2", "bg_3"]

    async def test_has_pending_reflects_running_state(self) -> None:
        mgr = BackgroundTaskSupervisor()

        async def slow() -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(output="done")

        assert not mgr.has_pending()
        alias = mgr.next_alias()
        mgr.launch(alias, "noop", {}, slow())
        assert mgr.has_pending()
        await mgr.cancel(alias, "")
        assert not mgr.has_pending()

# ---------------------------------------------------------------------------
# Live progress tail — append_progress / make_progress_callback / result tool
# ---------------------------------------------------------------------------


class TestLiveProgressTail:
    async def test_append_progress_buffers_running_lines(self) -> None:
        mgr = BackgroundTaskSupervisor()

        async def slow() -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(output="done")

        alias = mgr.next_alias()
        mgr.launch(alias, "noop", {}, slow())
        try:
            mgr.append_progress(alias, "first")
            mgr.append_progress(alias, "second\nthird")
            tail = mgr._tasks[alias].progress_lines[-3:]
            assert tail == ["first", "second", "third"]
        finally:
            await mgr.cancel(alias, "")

    async def test_append_progress_unknown_task_is_noop(self) -> None:
        mgr = BackgroundTaskSupervisor()
        mgr.append_progress("bg_nope", "ignored")  # must not raise

    async def test_append_progress_after_finish_is_noop(self) -> None:
        mgr = BackgroundTaskSupervisor()

        async def quick() -> ToolResult:
            return ToolResult(output="hi")

        alias = mgr.next_alias()
        mgr.launch(alias, "noop", {}, quick())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        before = list(mgr._tasks[alias].progress_lines)
        mgr.append_progress(alias, "late")
        assert mgr._tasks[alias].progress_lines == before

    async def test_make_progress_callback_round_trip(self) -> None:
        mgr = BackgroundTaskSupervisor()

        async def slow() -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(output="done")

        alias = mgr.next_alias()
        mgr.launch(alias, "noop", {}, slow())
        try:
            cb = mgr.make_progress_callback(alias)
            cb("alpha")
            cb("beta")
            assert mgr._tasks[alias].progress_lines[-2:] == ["alpha", "beta"]
        finally:
            await mgr.cancel(alias, "")

    async def test_check_result_surfaces_live_tail_for_running(self) -> None:
        tool = CheckBackgroundTaskResultTool()
        mgr = BackgroundTaskSupervisor()

        async def slow() -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(output="done")

        alias = mgr.next_alias()
        mgr.launch(alias, "noop", {}, slow())
        try:
            mgr.append_progress(alias, "live-1")
            mgr.append_progress(alias, "live-2")
            result = await tool.execute(CheckBackgroundTaskResultInput(task_id=alias), _ctx(mgr))
            payload = json.loads(result.output)
            assert payload["status"] == "running"
            assert payload["result"].endswith("live-1\nlive-2")
        finally:
            await mgr.cancel(alias, "")

    async def test_check_result_running_task_carries_start_stamp(self) -> None:
        tool = CheckBackgroundTaskResultTool()
        mgr = BackgroundTaskSupervisor()

        async def slow() -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(output="done")

        alias = mgr.next_alias()
        mgr.launch(alias, "noop", {}, slow())
        try:
            result = await tool.execute(CheckBackgroundTaskResultInput(task_id=alias), _ctx(mgr))
            payload = json.loads(result.output)
            assert payload["status"] == "running"
            assert payload["result"].startswith("[started:")
        finally:
            await mgr.cancel(alias, "")
