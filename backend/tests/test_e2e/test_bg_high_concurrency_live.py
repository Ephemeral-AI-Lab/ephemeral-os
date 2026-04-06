# ruff: noqa
"""Live E2E: High-concurrency background + foreground task mixing.

Tests that the LLM correctly manages multiple simultaneous background tasks
alongside multiple foreground operations under high concurrency pressure.

Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_bg_high_concurrency_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are test-concurrency-agent, a developer with a remote Daytona sandbox.

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
    bg_started = result.background_started()
    bg_completed = result.background_completed()
    checks = result.tool_count("check_background_progress")
    cancels = result.tool_count("cancel_background_task")

    logger.info(
        f"\n{'='*60}\n[{label}] Event summary:\n"
        f"  Total events: {len(result.events)}\n"
        f"  Tools started: {len(result.tools_started())}\n"
        f"  Tools completed: {len(result.tools_completed())}\n"
        f"  Background started: {len(bg_started)}\n"
        f"  Background completed: {len(bg_completed)}\n"
        f"  Progress checks: {checks}\n"
        f"  Cancels: {cancels}\n"
        f"  Tool sequence: {result.tool_names}\n"
        f"{'='*60}"
    )


# ===========================================================================
# Test 1: Three simultaneous background tasks
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestTripleBackgroundConcurrency:
    """Launch 3 background tasks simultaneously and manage them."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-triple")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_three_background_tasks_launched(self, sandbox):
        """LLM should launch 3 background tasks and do foreground work."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch THREE background tasks simultaneously:\n"
            "1. Run 'sleep 10 && echo BUILD_A_DONE' in background (background: true)\n"
            "2. Run 'sleep 15 && echo BUILD_B_DONE' in background (background: true)\n"
            "3. Run 'sleep 20 && echo BUILD_C_DONE' in background (background: true)\n\n"
            "While those run, do these foreground tasks:\n"
            "4. Run 'echo FG_WORK_1' in foreground\n"
            "5. Run 'echo FG_WORK_2' in foreground\n\n"
            "Then check progress on all background tasks using check_background_progress.\n"
            "Use background: true for steps 1-3 ONLY."
        )
        _log_result(result, "triple_bg")

        assert len(result.assistant_turns()) >= 1, "Missing assistant turn"
        assert len(result.background_started()) >= 3, \
            f"Expected 3+ background tasks started. Got {len(result.background_started())}"
        assert len(result.tools_started()) >= 5, \
            f"Expected 5+ total tool calls (3 bg + 2 fg). Got: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected check_background_progress. Got: {result.tool_names}"


# ===========================================================================
# Test 2: Interleaved background launches with foreground work
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestInterleavedBgFg:
    """Background tasks launched between foreground operations."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-interleave")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_interleaved_bg_fg_execution(self, sandbox):
        """Alternate between launching bg tasks and doing fg work."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Execute these steps IN ORDER:\n"
            "1. Run 'echo SETUP_DONE' in foreground\n"
            "2. Run 'sleep 15 && echo TESTS_DONE' in background (background: true)\n"
            "3. Run 'echo LINT_STARTED' in foreground\n"
            "4. Run 'sleep 20 && echo DEPLOY_DONE' in background (background: true)\n"
            "5. Run 'echo CONFIG_UPDATED' in foreground\n"
            "6. Check progress of all background tasks using check_background_progress\n"
            "7. Cancel all background tasks using cancel_background_task\n\n"
            "Use background: true ONLY for steps 2 and 4."
        )
        _log_result(result, "interleaved")

        assert len(result.background_started()) >= 2, \
            f"Expected 2+ background tasks. Got {len(result.background_started())}"
        fg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and not tc.input.get("background")]
        assert len(fg_bash) >= 3, \
            f"Expected 3+ foreground bash calls. Got {len(fg_bash)}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel. Got: {result.tool_names}"


# ===========================================================================
# Test 3: Multiple background tasks with file creation foreground
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestBgWithFileCreation:
    """Background builds while creating files in foreground."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-files")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_bg_tasks_with_fg_file_operations(self, sandbox):
        """Launch bg tasks, create multiple files in fg, then check/cancel."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do the following:\n"
            "1. Run 'sleep 15 && echo COMPILE_DONE' in background (background: true)\n"
            "2. Run 'sleep 25 && echo PACKAGE_DONE' in background (background: true)\n"
            "3. Create /workspace/config.json with '{\"version\": 1}' using daytona_write_file\n"
            "4. Create /workspace/readme.txt with 'Project README' using daytona_write_file\n"
            "5. Run 'ls /workspace/' in foreground to verify files\n"
            "6. Check background progress using check_background_progress\n"
            "7. Cancel all background tasks\n\n"
            "Use background: true for steps 1-2 ONLY."
        )
        _log_result(result, "bg_files")

        assert len(result.background_started()) >= 2, \
            f"Expected 2+ background tasks. Got {len(result.background_started())}"
        assert result.has_tool("daytona_write_file"), \
            f"Expected file creation. Got: {result.tool_names}"
        write_count = result.tool_count("daytona_write_file")
        assert write_count >= 2, \
            f"Expected 2+ file writes. Got {write_count}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel. Got: {result.tool_names}"


# ===========================================================================
# Test 4: High-volume foreground burst with background running
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestHighVolumeForegroundBurst:
    """Many rapid foreground operations while background tasks run."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-burst")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_ten_foreground_ops_with_two_background(self, sandbox):
        """2 background tasks + 10 foreground echo commands."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        fg_steps = "\n".join(
            f"{i+3}. Run 'echo FG_STEP_{i+1}' in foreground"
            for i in range(10)
        )
        result = await agent.invoke(
            "Execute ALL of these steps:\n"
            "1. Run 'sleep 30 && echo BG_BUILD_DONE' in background (background: true)\n"
            "2. Run 'sleep 45 && echo BG_TEST_DONE' in background (background: true)\n"
            f"{fg_steps}\n"
            "13. Check background progress using check_background_progress\n"
            "14. Cancel all background tasks\n\n"
            "Use background: true for steps 1-2 ONLY. Execute each step with daytona_bash."
        )
        _log_result(result, "burst")

        assert len(result.background_started()) >= 2, \
            f"Expected 2+ background tasks. Got {len(result.background_started())}"
        assert len(result.tools_started()) >= 8, \
            f"Expected 8+ total tool calls. Got {len(result.tools_started())}: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel. Got: {result.tool_names}"
        assert not result.has_errors, \
            f"Errors under high concurrency: {[e.output for e in result.error_events]}"


# ===========================================================================
# Test 5: Four background tasks — max concurrency stress
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestFourBackgroundMaxConcurrency:
    """Push concurrency limits with 4 background + 3 foreground tasks."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-max-concur")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_four_bg_three_fg(self, sandbox):
        """Launch 4 bg tasks, do 3 fg tasks, check all, cancel all."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Execute these steps:\n"
            "1. Run 'sleep 10 && echo LINT_DONE' in background (background: true)\n"
            "2. Run 'sleep 15 && echo UNIT_DONE' in background (background: true)\n"
            "3. Run 'sleep 20 && echo INTEG_DONE' in background (background: true)\n"
            "4. Run 'sleep 30 && echo E2E_DONE' in background (background: true)\n"
            "5. Run 'echo DEPLOYING_CONFIG' in foreground\n"
            "6. Create /workspace/deploy.log with 'deploy started' using daytona_write_file\n"
            "7. Run 'echo MIGRATION_DONE' in foreground\n"
            "8. Check all background task progress using check_background_progress\n"
            "9. Cancel ALL remaining background tasks\n"
            "10. Report how many background tasks were running\n\n"
            "Use background: true for steps 1-4 ONLY."
        )
        _log_result(result, "max_concurrency")

        assert len(result.background_started()) >= 4, \
            f"Expected 4+ background tasks. Got {len(result.background_started())}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel. Got: {result.tool_names}"
        assert len(result.tools_started()) >= 7, \
            f"Expected 7+ tool calls total. Got {len(result.tools_started())}"
        assert not result.has_errors, \
            f"Errors under max concurrency: {[e.output[:200] for e in result.error_events]}"
