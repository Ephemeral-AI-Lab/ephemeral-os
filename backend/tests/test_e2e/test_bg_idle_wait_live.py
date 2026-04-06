# ruff: noqa
"""Live E2E: Idle and wait scenarios — waiting for background tasks to complete.

Tests that the LLM correctly handles idle periods while waiting for
background tasks, including polling, waiting, and reacting to completion.

Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_bg_idle_wait_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are test-idle-agent, a developer with a remote Daytona sandbox.

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
- When all foreground work is done, poll background tasks until they complete or you decide to cancel.

Always be concise. Execute tools, don't just describe them.
"""


def _log_result(result, label: str) -> None:
    checks = result.tool_count("check_background_progress")
    cancels = result.tool_count("cancel_background_task")

    logger.info(
        f"\n{'='*60}\n[{label}] Idle/Wait summary:\n"
        f"  Tools started: {len(result.tools_started())}\n"
        f"  Background started: {len(result.background_started())}\n"
        f"  Background completed: {len(result.background_completed())}\n"
        f"  Progress checks: {checks}\n"
        f"  Cancels: {cancels}\n"
        f"  Tool sequence: {result.tool_names}\n"
        f"{'='*60}"
    )


# ===========================================================================
# Test 1: Wait for a short background task to complete
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestWaitForShortTask:
    """LLM waits and polls until a short background task finishes."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-short")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_poll_until_short_task_completes(self, sandbox):
        """Background a 5s task, do minimal fg work, keep checking until done."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 5 && echo QUICK_BUILD_DONE' in background (background: true)\n"
            "2. Run 'echo PREP_DONE' in foreground\n"
            "3. Now wait for the background task to finish. Keep checking progress "
            "using check_background_progress until it shows as completed.\n"
            "4. Once the task is done, report the final output.\n\n"
            "Use background: true for step 1 ONLY. "
            "You MUST call check_background_progress at least once."
        )
        _log_result(result, "wait_short")

        assert len(result.background_started()) >= 1, \
            f"Expected background task. Got: {result.tool_names}"
        checks = result.tool_count("check_background_progress")
        assert checks >= 1, \
            f"Expected 1+ progress checks while waiting. Got {checks}"
        assert len(result.assistant_turns()) >= 1, "Missing final report"


# ===========================================================================
# Test 2: Foreground work exhausted — idle polling on background
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestIdleAfterForegroundExhausted:
    """All foreground work done, LLM enters idle polling mode."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-exhausted")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_idle_polling_after_all_fg_done(self, sandbox):
        """Finish all fg work quickly, then poll bg task."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 20 && echo INTEGRATION_TESTS_DONE' in background (background: true)\n"
            "2. Run 'echo FG_TASK_1' in foreground\n"
            "3. Run 'echo FG_TASK_2' in foreground\n"
            "4. That's all the foreground work. Now you are idle.\n"
            "5. Check background progress using check_background_progress\n"
            "6. The task is still running — check again\n"
            "7. It's still running and taking too long — cancel it with "
            "cancel_background_task using reason: 'Timed out waiting'\n"
            "8. Report what happened\n\n"
            "Use background: true for step 1 ONLY."
        )
        _log_result(result, "idle_exhausted")

        assert len(result.background_started()) >= 1, \
            f"Expected background task. Got: {result.tool_names}"
        checks = result.tool_count("check_background_progress")
        assert checks >= 1, \
            f"Expected 1+ idle progress checks. Got {checks}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel after idle timeout. Got: {result.tool_names}"


# ===========================================================================
# Test 3: Wait for two background tasks — staggered completion
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestStaggeredCompletion:
    """Two bg tasks with different durations — fast one finishes first."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-stagger")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_wait_staggered_bg_tasks(self, sandbox):
        """Launch fast + slow bg tasks, poll, see fast finish, cancel slow."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 5 && echo FAST_DONE' in background (background: true)\n"
            "2. Run 'sleep 60 && echo SLOW_DONE' in background (background: true)\n"
            "3. Run 'echo PREP_COMPLETE' in foreground\n"
            "4. Check background progress — one might be done already\n"
            "5. Check background progress again\n"
            "6. The slow task is still running — cancel it with cancel_background_task "
            "using reason: 'Slow task not needed anymore'\n"
            "7. Report: which task finished and which was cancelled?\n\n"
            "Use background: true for steps 1-2 ONLY."
        )
        _log_result(result, "staggered")

        assert len(result.background_started()) >= 2, \
            f"Expected 2 background tasks. Got {len(result.background_started())}"
        checks = result.tool_count("check_background_progress")
        assert checks >= 2, \
            f"Expected 2+ progress checks. Got {checks}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel of slow task. Got: {result.tool_names}"


# ===========================================================================
# Test 4: Idle with no foreground work — pure background monitoring
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestPureBackgroundMonitoring:
    """No foreground tasks — LLM's only job is to monitor background tasks."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-pure")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_monitor_only_no_fg_work(self, sandbox):
        """Launch bg tasks, provide NO fg work — LLM must poll and manage."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch these background tasks and monitor them:\n"
            "1. Run 'sleep 5 && echo MONITOR_A_DONE' in background (background: true)\n"
            "2. Run 'sleep 45 && echo MONITOR_B_DONE' in background (background: true)\n\n"
            "There is NO foreground work to do. Your job is to:\n"
            "- Check progress using check_background_progress\n"
            "- If the first task completes, note it\n"
            "- The second task is too slow — cancel it using cancel_background_task "
            "with reason: 'Monitor timeout exceeded'\n"
            "- Report final status of both tasks\n\n"
            "Use background: true for steps 1-2."
        )
        _log_result(result, "pure_monitor")

        assert len(result.background_started()) >= 2, \
            f"Expected 2 background tasks. Got {len(result.background_started())}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress monitoring. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel of slow task. Got: {result.tool_names}"


# ===========================================================================
# Test 5: Wait then act — use background result to drive next action
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestWaitThenAct:
    """Wait for bg task, then use its result to decide next action."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-then-act")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_bg_result_drives_next_action(self, sandbox):
        """Background a task, wait for result, then act based on output."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 3 && echo BUILD_SUCCESS' in background (background: true)\n"
            "2. Run 'echo PREPARING_DEPLOY' in foreground\n"
            "3. Check background progress using check_background_progress\n"
            "4. If the background build succeeded (output contains 'BUILD_SUCCESS'), "
            "create /workspace/deploy_ready.txt with 'READY_TO_DEPLOY' using daytona_write_file\n"
            "5. If the build is still running, check progress again, then create the file\n"
            "6. Run 'cat /workspace/deploy_ready.txt' in foreground to verify\n"
            "7. Report the complete workflow\n\n"
            "Use background: true for step 1 ONLY."
        )
        _log_result(result, "wait_then_act")

        assert len(result.background_started()) >= 1, \
            f"Expected background task. Got: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        assert result.has_tool("daytona_write_file"), \
            f"Expected file creation after bg result. Got: {result.tool_names}"
        assert len(result.tools_started()) >= 4, \
            f"Expected 4+ tool calls. Got {len(result.tools_started())}"
