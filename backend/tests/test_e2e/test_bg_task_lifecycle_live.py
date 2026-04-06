# ruff: noqa
"""Live E2E: Task lifecycle management — progress checks, cancellation, notifications.

Tests thorough usage of check_background_progress, cancel_background_task,
and background task completion notifications across various scenarios.

Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_bg_task_lifecycle_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are test-lifecycle-agent, a developer with a remote Daytona sandbox.

IMPORTANT RULES:
- You MUST use tools for every action — never just describe what you'd do.
- Use daytona_bash to run commands, daytona_write_file to create files.
- You have background task support: add "background": true to tool input for long-running operations.
- Use check_background_progress to monitor background tasks.
- Use cancel_background_task to cancel running background tasks.

BACKGROUND EXECUTION GUIDELINES:
- For commands that take >5 seconds (test suites, builds, npm install), run in background.
- For quick commands (<5 seconds like echo, pwd, cat), run in foreground.
- When running in background, continue with other useful work.
- Periodically check progress of background tasks.
- Cancel background tasks that appear stuck or failing.

Always be concise. Execute tools, don't just describe them.
"""


def _log_result(result, label: str) -> None:
    checks = result.tool_count("check_background_progress")
    cancels = result.tool_count("cancel_background_task")
    bg_completed = result.background_completed()

    logger.info(
        f"\n{'='*60}\n[{label}] Lifecycle summary:\n"
        f"  Tools started: {len(result.tools_started())}\n"
        f"  Background started: {len(result.background_started())}\n"
        f"  Background completed: {len(bg_completed)}\n"
        f"  Progress checks: {checks}\n"
        f"  Cancels: {cancels}\n"
        f"  Tool sequence: {result.tool_names}\n"
        f"{'='*60}"
    )


# ===========================================================================
# Test 1: Multiple progress checks on a single background task
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestRepeatedProgressChecks:
    """LLM checks progress multiple times on a running background task."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("lc-repeated")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_multiple_progress_checks_before_cancel(self, sandbox):
        """Background a long task, check progress 3+ times, then cancel."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 60 && echo LONG_BUILD_DONE' in background (background: true)\n"
            "2. Run 'echo WORK_A' in foreground\n"
            "3. Call check_background_progress to check the background task\n"
            "4. Run 'echo WORK_B' in foreground\n"
            "5. Call check_background_progress again\n"
            "6. Run 'echo WORK_C' in foreground\n"
            "7. Call check_background_progress a third time\n"
            "8. The build is taking too long — cancel it with cancel_background_task "
            "using reason: 'Build exceeded time budget'\n"
            "9. Report the status from each progress check\n\n"
            "You MUST call check_background_progress THREE times (steps 3, 5, 7). "
            "Use background: true for step 1 ONLY."
        )
        _log_result(result, "repeated_checks")

        assert len(result.background_started()) >= 1, \
            f"Expected background task. Got: {result.tool_names}"
        checks = result.tool_count("check_background_progress")
        assert checks >= 2, \
            f"Expected 2+ progress checks. Got {checks}: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel. Got: {result.tool_names}"


# ===========================================================================
# Test 2: Selective cancellation — cancel one, keep another
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestSelectiveCancellation:
    """Cancel one background task while keeping another running."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("lc-selective")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_cancel_one_keep_one(self, sandbox):
        """Launch 2 bg tasks, cancel the slow one, keep the fast one."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 8 && echo FAST_TEST_DONE' in background (background: true) — this is the fast test\n"
            "2. Run 'sleep 120 && echo SLOW_DEPLOY_DONE' in background (background: true) — this is the slow deploy\n"
            "3. Run 'echo WORKING_ON_FIX' in foreground\n"
            "4. Check background progress using check_background_progress\n"
            "5. Cancel ONLY the slow deploy task (the one with 'sleep 120') using cancel_background_task "
            "with reason: 'Deploy is taking too long, cancelling'\n"
            "6. Check progress again to confirm the fast test is still running or completed\n"
            "7. Report which tasks are still active\n\n"
            "Use background: true for steps 1 and 2 ONLY."
        )
        _log_result(result, "selective_cancel")

        assert len(result.background_started()) >= 2, \
            f"Expected 2 background tasks. Got {len(result.background_started())}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel_background_task. Got: {result.tool_names}"
        checks = result.tool_count("check_background_progress")
        assert checks >= 2, \
            f"Expected 2+ progress checks. Got {checks}"


# ===========================================================================
# Test 3: Cancel all background tasks in batch
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestBatchCancellation:
    """Cancel multiple background tasks one after another."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("lc-batch-cancel")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_cancel_three_tasks_sequentially(self, sandbox):
        """Launch 3 bg tasks, check progress, then cancel each one."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Execute these steps:\n"
            "1. Run 'sleep 30 && echo TASK_A' in background (background: true)\n"
            "2. Run 'sleep 40 && echo TASK_B' in background (background: true)\n"
            "3. Run 'sleep 50 && echo TASK_C' in background (background: true)\n"
            "4. Run 'echo FG_DONE' in foreground\n"
            "5. Check progress of all tasks using check_background_progress\n"
            "6. Cancel ALL three background tasks one by one using cancel_background_task. "
            "Use reason: 'Batch cleanup — cancelling all pending tasks' for each.\n"
            "7. Check progress again to confirm all cancelled\n"
            "8. Report final state\n\n"
            "Use background: true for steps 1-3 ONLY."
        )
        _log_result(result, "batch_cancel")

        assert len(result.background_started()) >= 3, \
            f"Expected 3 background tasks. Got {len(result.background_started())}"
        cancels = result.tool_count("cancel_background_task")
        assert cancels >= 2, \
            f"Expected 2+ cancellations. Got {cancels}: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"


# ===========================================================================
# Test 4: Background task notification — short task completes naturally
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestBackgroundCompletion:
    """Background task completes while LLM does foreground work."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("lc-notify")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_bg_task_completes_during_fg_work(self, sandbox):
        """Short bg task should complete while LLM does enough fg work."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 3 && echo SHORT_BG_DONE' in background (background: true)\n"
            "2. Run 'echo FG_1' in foreground\n"
            "3. Run 'echo FG_2' in foreground\n"
            "4. Run 'echo FG_3' in foreground\n"
            "5. Run 'echo FG_4' in foreground\n"
            "6. Run 'echo FG_5' in foreground\n"
            "7. Check background progress — the task should be done by now\n"
            "8. Report whether the background task completed successfully\n\n"
            "Use background: true for step 1 ONLY."
        )
        _log_result(result, "bg_completion")

        assert len(result.background_started()) >= 1, \
            f"Expected background task. Got: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        # The short task should have completed — check for completion event or
        # the LLM reporting it as done
        assert len(result.tools_started()) >= 6, \
            f"Expected 6+ tool calls. Got {len(result.tools_started())}"


# ===========================================================================
# Test 5: Progress check reveals error — LLM reacts appropriately
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestProgressCheckRevealsError:
    """Background task that fails — LLM should detect and handle the error."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("lc-error")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_bg_failure_detected_on_progress_check(self, sandbox):
        """Run a bg task that fails fast, check progress, react to the error."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 2 && exit 1' in background (background: true) — "
            "this simulates a failing test suite\n"
            "2. Run 'echo DOING_OTHER_WORK' in foreground\n"
            "3. Run 'echo MORE_WORK' in foreground\n"
            "4. Check background progress using check_background_progress\n"
            "5. Based on what you see, report whether the background task succeeded or failed\n"
            "6. If the task failed, explain what happened\n\n"
            "Use background: true for step 1 ONLY."
        )
        _log_result(result, "bg_error")

        assert len(result.background_started()) >= 1, \
            f"Expected background task. Got: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        assert len(result.assistant_turns()) >= 1, \
            "Expected assistant to report on the error"
