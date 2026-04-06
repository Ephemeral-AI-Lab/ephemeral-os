# ruff: noqa
"""Live E2E: Background task progress checking and output handling.

Tests check_background_progress, wait_for_background_task output truncation,
multi-task status visibility, and task-id-based filtering.

Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_bg_progress_output_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are test-progress-agent, a developer with a remote Daytona sandbox.

IMPORTANT RULES:
- You MUST use tools for every action — never just describe what you'd do.
- Use daytona_bash to run commands, daytona_write_file to create files.
- You have background task support: add "background": true to tool input for long-running operations.
- Use check_background_progress to get an instant status snapshot of background tasks.
- Use wait_for_background_task to block until background tasks complete (only when no foreground work remains).
- Use cancel_background_task to cancel running background tasks.

BACKGROUND WORKFLOW:
1. Launch background tasks with "background": true
2. Do any foreground work while background runs
3. Call check_background_progress for quick status snapshots
4. When idle with no foreground work, use wait_for_background_task to block
5. Use cancel_background_task for tasks taking too long

Always be concise. Execute tools, don't just describe them.
"""


def _log_result(result, label: str) -> None:
    checks = result.tool_count("check_background_progress")
    waits = result.tool_count("wait_for_background_task")
    cancels = result.tool_count("cancel_background_task")
    bg_completed = result.background_completed()

    logger.info(
        f"\n{'='*60}\n[{label}] Progress/output summary:\n"
        f"  Tools started: {len(result.tools_started())}\n"
        f"  Background started: {len(result.background_started())}\n"
        f"  Background completed: {len(bg_completed)}\n"
        f"  Progress checks: {checks}\n"
        f"  Waits: {waits}\n"
        f"  Cancels: {cancels}\n"
        f"  Tool sequence: {result.tool_names}\n"
        f"{'='*60}"
    )


# ===========================================================================
# Test 1: check_background_progress shows running task with elapsed time
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestCheckProgressRunningStatus:
    """LLM checks progress of a running background task and sees elapsed time."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("prog-running")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_check_shows_running_tasks_with_elapsed(self, sandbox):
        """Launch a long task, do fg work, check progress to see running status, then cancel."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 'sleep 30 && echo LONG_TASK' in background (background: true). "
            "Do 'echo FG_DONE' in foreground. "
            "Now call check_background_progress to see the status. "
            "The task should show as 'running' with elapsed time. "
            "Then cancel it with cancel_background_task using reason 'Test complete'. "
            "Report what status you saw."
        )
        _log_result(result, "running_status")

        assert result.has_tool("check_background_progress"), \
            f"Expected check_background_progress. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel_background_task. Got: {result.tool_names}"
        text_lower = result.text.lower()
        assert any(word in text_lower for word in ["running", "elapsed", "progress"]), \
            f"Expected text to mention running/elapsed/progress. Got: {result.text[:300]}"
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 2: check_background_progress shows completed task output
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestCheckProgressCompletedOutput:
    """LLM waits for a task to complete, then checks progress to see output."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("prog-completed")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_check_shows_completed_task_output(self, sandbox):
        """Launch a short task with output, wait for it, then check progress for the output."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 'sleep 3 && echo \"LINE1\\nLINE2\\nLINE3\\nRESULT_OK\"' in background (background: true). "
            "Do 'echo WAITING' in foreground. "
            "Use wait_for_background_task with timeout=10 to wait for it to complete. "
            "Then call check_background_progress to see the completed output. "
            "Report what lines you see in the output."
        )
        _log_result(result, "completed_output")

        assert result.has_tool("wait_for_background_task"), \
            f"Expected wait_for_background_task. Got: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected check_background_progress. Got: {result.tool_names}"
        text_lower = result.text.lower()
        assert any(word in text_lower for word in ["result_ok", "line", "output", "complet"]), \
            f"Expected text to mention RESULT_OK/LINE/output/complet. Got: {result.text[:300]}"
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 3: check_background_progress with last_n_lines truncation
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestCheckProgressLastNLines:
    """LLM uses last_n_lines parameter on check_background_progress."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("prog-lastn")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_check_with_last_n_lines_truncation(self, sandbox):
        """Launch a task that produces 50 lines, wait for it, then check with last_n_lines=5."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch this command in background (background: true): "
            "'for i in $(seq 1 50); do echo \"LOG_LINE_$i\"; done'. "
            "Wait for it with wait_for_background_task timeout=10. "
            "Then call check_background_progress with last_n_lines=5. "
            "Report how many lines you see in the output."
        )
        _log_result(result, "last_n_lines")

        assert result.has_tool("wait_for_background_task"), \
            f"Expected wait_for_background_task. Got: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected check_background_progress. Got: {result.tool_names}"
        # Verify the check call included last_n_lines in its input
        check_calls = [tc for tc in result.tool_calls if tc.name == "check_background_progress"]
        assert any("last_n_lines" in tc.input for tc in check_calls), \
            f"Expected at least one check_background_progress call with last_n_lines. " \
            f"Got inputs: {[tc.input for tc in check_calls]}"
        text_lower = result.text.lower()
        assert any(word in text_lower for word in ["lines", "output", "truncat"]), \
            f"Expected text to mention lines/output/truncat. Got: {result.text[:300]}"
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 4: check_background_progress shows all tasks' status
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestCheckProgressMultipleTasks:
    """LLM launches 2 tasks and sees both in check_background_progress."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("prog-multi")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_check_shows_all_tasks_status(self, sandbox):
        """Launch 2 bg tasks, check both show up, wait for short one, cancel long one."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 'sleep 3 && echo TASK_A_RESULT' in background (background: true) AND "
            "'sleep 30 && echo TASK_B_RESULT' in background (background: true). "
            "Do 'echo FG' in foreground. "
            "Check progress with check_background_progress — you should see 2 tasks. "
            "Wait for any task with wait_for_background_task timeout=10. "
            "Check progress again — one should be completed, one still running. "
            "Cancel the running one with cancel_background_task. "
            "Report status of both tasks."
        )
        _log_result(result, "multi_tasks")

        bg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and tc.input.get("background") is True]
        assert len(bg_bash) >= 2, \
            f"Expected 2+ background launches. Got {len(bg_bash)}: {result.tool_names}"
        checks = result.tool_count("check_background_progress")
        assert checks >= 2, \
            f"Expected 2+ check_background_progress calls. Got {checks}: {result.tool_names}"
        assert result.has_tool("wait_for_background_task"), \
            f"Expected wait_for_background_task. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel_background_task. Got: {result.tool_names}"
        text_lower = result.text.lower()
        assert any(word in text_lower for word in ["complet", "done", "result"]), \
            f"Expected text to mention completed task. Got: {result.text[:300]}"
        assert any(word in text_lower for word in ["cancel", "running", "stop"]), \
            f"Expected text to mention cancelled/running task. Got: {result.text[:300]}"
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 5: check_background_progress filtered by task_id
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestCheckProgressFilterByTaskId:
    """LLM uses task_id parameter on check_background_progress for targeted queries."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("prog-taskid")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_check_specific_task_by_id(self, sandbox):
        """Launch 2 tasks, do a full check, then check only the completed task by its id."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 'sleep 3 && echo ALPHA_DONE' in background (background: true) AND "
            "'sleep 30 && echo BETA_DONE' in background (background: true). "
            "Do 'echo PREP' in foreground. "
            "Check progress for all tasks first (no task_id). "
            "Then wait for the short task with wait_for_background_task timeout=10. "
            "After it completes, check progress for just the completed task using its task_id. "
            "Cancel the other task with cancel_background_task. "
            "Report what you saw."
        )
        _log_result(result, "filter_by_taskid")

        bg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and tc.input.get("background") is True]
        assert len(bg_bash) >= 2, \
            f"Expected 2+ background launches. Got {len(bg_bash)}: {result.tool_names}"
        checks = result.tool_count("check_background_progress")
        assert checks >= 2, \
            f"Expected 2+ check_background_progress calls. Got {checks}: {result.tool_names}"
        check_calls = [tc for tc in result.tool_calls if tc.name == "check_background_progress"]
        assert any("task_id" in tc.input for tc in check_calls), \
            f"Expected at least one check_background_progress call with task_id. " \
            f"Got inputs: {[tc.input for tc in check_calls]}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel_background_task. Got: {result.tool_names}"
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 6: wait_for_background_task with last_n_lines truncation
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestWaitLastNLinesOutput:
    """LLM uses last_n_lines parameter on wait_for_background_task."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("wait-lastn")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_wait_returns_truncated_output(self, sandbox):
        """Launch a task producing 100 lines, wait with last_n_lines=10, report lines seen."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 'for i in $(seq 1 100); do echo \"BUILD_LOG_$i\"; done' in background (background: true). "
            "Do 'echo PREP' in foreground. "
            "Check progress with check_background_progress. "
            "Then call wait_for_background_task with timeout=10 and last_n_lines=10. "
            "Report how many output lines you received."
        )
        _log_result(result, "wait_last_n")

        assert result.has_tool("wait_for_background_task"), \
            f"Expected wait_for_background_task. Got: {result.tool_names}"
        wait_calls = [tc for tc in result.tool_calls if tc.name == "wait_for_background_task"]
        assert any("last_n_lines" in tc.input for tc in wait_calls), \
            f"Expected wait_for_background_task call with last_n_lines. " \
            f"Got inputs: {[tc.input for tc in wait_calls]}"
        text_lower = result.text.lower()
        assert any(word in text_lower for word in ["build_log", "lines", "output"]), \
            f"Expected text to mention BUILD_LOG/lines/output. Got: {result.text[:300]}"
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"
