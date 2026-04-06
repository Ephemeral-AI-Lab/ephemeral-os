# ruff: noqa
"""Live E2E: LLM autonomous background task decision-making.

Tests that the LLM independently decides when to check and cancel
background tasks — NO explicit instructions to check or cancel.

Uses EvalAgent for credential loading and agent configuration.
Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_background_autonomy_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging

import pytest

from engine.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are test-autonomy-agent, a developer with a remote Daytona sandbox.

RULES:
- Use tools for every action.
- Use daytona_bash to run commands.
- You have background task support: add "background": true to tool input for long operations.
- Use check_background_progress to check background tasks.
- Use cancel_background_task to cancel background tasks.
- Use your own judgment on when to check or cancel background tasks.
- Be concise.
"""


def _log_result(result, label: str) -> None:
    checks = result.tool_count("check_background_progress")
    cancels = result.tool_count("cancel_background_task")

    logger.info(
        f"\n{'='*60}\n[{label}]\n"
        f"  Tools: {len(result.tools_started())} started, {len(result.tools_completed())} completed\n"
        f"  Turns: {len(result.assistant_turns())}\n"
        f"  Sequence: {result.tool_names}\n"
        f"  LLM autonomous decisions: {checks} progress checks, {cancels} cancels\n"
        f"{'='*60}"
    )


# ===========================================================================
# Test 1: LLM decides on its own to check background progress
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestAutonomousProgressCheck:
    """No instruction to check — LLM decides on its own."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("auto-check")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_llm_autonomously_checks_progress(self, sandbox):
        """Give the LLM a background task and foreground work.
        Do NOT tell it to check progress. See if it does on its own."""
        agent = EvalAgent.create(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "I need you to do two things:\n"
            "- Run a long build: 'sleep 20 && echo BUILD_OK' in background\n"
            "- While it runs, create a file /workspace/readme.txt with "
            "'Hello World' using daytona_bash: echo 'Hello World' > /workspace/readme.txt\n"
            "- Then read it back: cat /workspace/readme.txt\n\n"
            "Let me know when everything is done."
        )
        _log_result(result, "autonomous_check")

        assert len(result.assistant_turns()) >= 1, "Missing assistant turn"

        has_check = result.has_tool("check_background_progress")
        if has_check:
            logger.info("[RESULT] LLM AUTONOMOUSLY checked background progress")
        else:
            logger.info("[RESULT] LLM did NOT check progress on its own")

        assert len(result.tool_names) >= 2, f"Expected 2+ tools. Got: {result.tool_names}"
        logger.info(f"[DONE] Autonomous check test: checked={has_check}")


# ===========================================================================
# Test 2: LLM decides on its own to cancel a hanging task
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestAutonomousCancel:
    """Background a task that will never finish. LLM must decide on its own."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("auto-cancel")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_llm_autonomously_handles_long_task(self, sandbox):
        """Background a very long task. Give foreground work. See what happens."""
        agent = EvalAgent.create(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Run 'sleep 120 && echo NEVER_FINISHES' in background.\n"
            "Then run 'echo quick_task_done' in foreground.\n\n"
            "The background task simulates a very slow npm install. "
            "Use your judgment on what to do about it."
        )
        _log_result(result, "autonomous_cancel")

        assert len(result.assistant_turns()) >= 1, "Missing assistant turn"

        has_check = result.has_tool("check_background_progress")
        has_cancel = result.has_tool("cancel_background_task")

        logger.info(
            f"[RESULT] LLM autonomous decisions: checked={has_check}, cancelled={has_cancel}"
        )

        if has_cancel:
            logger.info("[RESULT] LLM AUTONOMOUSLY cancelled the long task")
        elif has_check:
            logger.info("[RESULT] LLM checked progress but decided to wait")
        else:
            logger.info("[RESULT] LLM did not interact with background task")

        assert len(result.tool_names) >= 1, f"Expected 1+ tools. Got: {result.tool_names}"


# ===========================================================================
# Test 3: Multi-task autonomy — LLM manages two background tasks
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestAutonomousMultiTask:
    """Two background tasks. LLM must manage them independently."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("auto-multi")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_llm_manages_multiple_background_tasks(self, sandbox):
        """Two background tasks with different durations. See how LLM manages."""
        agent = EvalAgent.create(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "I need two things running in the background:\n"
            "- A fast build: 'sleep 10 && echo FAST_BUILD_DONE' in background\n"
            "- A slow test suite: 'sleep 60 && echo SLOW_TESTS_DONE' in background\n\n"
            "While those run, create /workspace/status.txt with 'waiting for builds' "
            "using daytona_bash.\n\n"
            "Manage the background tasks as you see fit."
        )
        _log_result(result, "autonomous_multi")

        assert len(result.assistant_turns()) >= 1, "Missing assistant turn"

        checks = result.tool_count("check_background_progress")
        cancels = result.tool_count("cancel_background_task")
        bash_calls = result.tool_count("daytona_bash")

        logger.info(
            f"[RESULT] Multi-task autonomy:\n"
            f"  bash calls: {bash_calls}\n"
            f"  progress checks: {checks}\n"
            f"  cancels: {cancels}\n"
            f"  total tools: {len(result.tool_names)}"
        )

        assert bash_calls >= 1, f"Expected 1+ bash calls. Got: {result.tool_names}"
        logger.info(f"[DONE] Multi-task autonomy: {len(result.tool_names)} total tools")
