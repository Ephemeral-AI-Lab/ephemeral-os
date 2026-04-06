# ruff: noqa
"""Live E2E: Mixed chaos scenarios — complex interleaving of bg/fg with errors,
cancellations, re-launches, and notification handling.

Tests the most complex real-world scenarios where background and foreground tasks
interact in unpredictable ways.

Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_bg_mixed_chaos_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are test-chaos-agent, a developer with a remote Daytona sandbox.

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
- Use your judgment — cancel and re-launch tasks when it makes sense.

Always be concise. Execute tools, don't just describe them.
"""


def _log_result(result, label: str) -> None:
    checks = result.tool_count("check_background_progress") + result.tool_count("wait_for_background_task")
    cancels = result.tool_count("cancel_background_task")
    bg_started = result.background_started()
    bg_completed = result.background_completed()

    logger.info(
        f"\n{'='*60}\n[{label}] Chaos summary:\n"
        f"  Total events: {len(result.events)}\n"
        f"  Tools started: {len(result.tools_started())}\n"
        f"  Tools completed: {len(result.tools_completed())}\n"
        f"  Background started: {len(bg_started)}\n"
        f"  Background completed: {len(bg_completed)}\n"
        f"  Progress checks: {checks}\n"
        f"  Cancels: {cancels}\n"
        f"  Has errors: {result.has_errors}\n"
        f"  Tool sequence: {result.tool_names}\n"
        f"{'='*60}"
    )


# ===========================================================================
# Test 1: Cancel and re-launch — fix then retry
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestCancelAndRelaunch:
    """Cancel a failing bg task, fix something, then re-launch."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("chaos-relaunch")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_cancel_fix_relaunch(self, sandbox):
        """Cancel a bg task, do a fg fix, launch a new bg task."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Simulate a fix-and-retry workflow:\n"
            "1. Run 'sleep 30 && echo TESTS_V1_DONE' in background (background: true) "
            "— this is the first test run\n"
            "2. Run 'echo CHECKING_LOGS' in foreground\n"
            "3. Check background progress using check_background_progress\n"
            "4. The tests are wrong — cancel the first bg task using cancel_background_task "
            "with reason: 'Tests running against wrong config'\n"
            "5. Run 'echo APPLYING_FIX' in foreground — simulating applying a fix\n"
            "6. Create /home/daytona/fix_applied.txt with 'config_v2' using daytona_write_file\n"
            "7. Run 'sleep 10 && echo TESTS_V2_DONE' in background (background: true) "
            "— re-run tests with fix\n"
            "8. Check background progress on the new task\n"
            "9. Report the workflow\n\n"
            "Use background: true for steps 1 and 7 ONLY."
        )
        _log_result(result, "cancel_relaunch")

        assert len(result.background_started()) >= 2, \
            f"Expected 2 background launches (original + retry). Got {len(result.background_started())}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel. Got: {result.tool_names}"
        checks = result.tool_count("check_background_progress") + result.tool_count("wait_for_background_task")
        assert checks >= 2, \
            f"Expected 2+ progress/wait checks. Got {checks}"
        assert result.has_tool("daytona_write_file"), \
            f"Expected file creation for fix. Got: {result.tool_names}"


# ===========================================================================
# Test 2: Background task with error + foreground error recovery
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestBgErrorFgRecovery:
    """Background task fails, LLM detects via progress check, fixes in foreground."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("chaos-recover")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_detect_bg_error_recover_in_fg(self, sandbox):
        """Bg task exits with error, LLM checks progress, does fg recovery."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps:\n"
            "1. Run 'sleep 2 && echo ERROR_FOUND >&2 && exit 1' in background "
            "(background: true) — this simulates a build that fails after 2 seconds\n"
            "2. Run 'echo WORKING_ON_FEATURE' in foreground\n"
            "3. Run 'echo STILL_WORKING' in foreground\n"
            "4. Check background progress using check_background_progress\n"
            "5. The background build failed! Create /home/daytona/error_report.txt "
            "with 'Build failed — needs investigation' using daytona_write_file\n"
            "6. Run 'echo RECOVERY_COMPLETE' in foreground\n"
            "7. Report: what was the error and what recovery steps were taken?\n\n"
            "Use background: true for step 1 ONLY."
        )
        _log_result(result, "bg_error_recovery")

        assert len(result.background_started()) >= 1, \
            f"Expected background task. Got: {result.tool_names}"
        assert result.has_tool("check_background_progress"), \
            f"Expected progress check. Got: {result.tool_names}"
        assert result.has_tool("daytona_write_file"), \
            f"Expected error report creation. Got: {result.tool_names}"
        assert len(result.tools_started()) >= 5, \
            f"Expected 5+ tool calls. Got {len(result.tools_started())}"


# ===========================================================================
# Test 3: Mixed bg/fg pipeline — build, test, deploy simulation
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestFullPipelineSimulation:
    """Simulate a full CI/CD pipeline: build(bg) -> lint(fg) -> test(bg) -> deploy(fg)."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("chaos-pipeline")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_cicd_pipeline_mixed_bg_fg(self, sandbox):
        """Full pipeline simulation with bg builds/tests and fg lint/deploy."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Simulate a CI/CD pipeline:\n\n"
            "Phase 1 — Build:\n"
            "1. Run 'sleep 8 && echo BUILD_ARTIFACTS_READY' in background (background: true)\n"
            "2. While build runs, run 'echo LINT_CHECK_PASS' in foreground (lint check)\n"
            "3. Run 'echo TYPE_CHECK_PASS' in foreground (type check)\n\n"
            "Phase 2 — Verify build:\n"
            "4. Check background progress using check_background_progress\n\n"
            "Phase 3 — Test:\n"
            "5. Run 'sleep 30 && echo ALL_TESTS_PASS' in background (background: true)\n"
            "6. While tests run, run 'echo DOCS_GENERATED' in foreground\n"
            "7. Create /home/daytona/changelog.txt with 'v2.0 release' using daytona_write_file\n\n"
            "Phase 4 — Check and cleanup:\n"
            "8. Check background progress on the test task\n"
            "9. Tests are taking too long — cancel with cancel_background_task "
            "using reason: 'Pipeline timeout'\n"
            "10. Run 'echo PIPELINE_COMPLETE' in foreground\n"
            "11. Report pipeline summary\n\n"
            "Use background: true for steps 1 and 5 ONLY."
        )
        _log_result(result, "pipeline")

        assert len(result.background_started()) >= 2, \
            f"Expected 2 background phases. Got {len(result.background_started())}"
        checks = result.tool_count("check_background_progress") + result.tool_count("wait_for_background_task")
        assert checks >= 2, \
            f"Expected 2+ progress/wait checks. Got {checks}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected pipeline cancel. Got: {result.tool_names}"
        assert result.has_tool("daytona_write_file"), \
            f"Expected changelog creation. Got: {result.tool_names}"
        assert len(result.tools_started()) >= 8, \
            f"Expected 8+ total tool calls. Got {len(result.tools_started())}"


# ===========================================================================
# Test 4: Rapid fire — launch, check, cancel, relaunch in quick succession
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestRapidFireLifecycle:
    """Rapid launch-check-cancel-relaunch cycles."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("chaos-rapid")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_rapid_launch_cancel_relaunch(self, sandbox):
        """Quick succession of bg task lifecycle operations."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Execute this rapid lifecycle:\n"
            "1. Run 'sleep 60 && echo ATTEMPT_1' in background (background: true)\n"
            "2. Immediately check progress using check_background_progress\n"
            "3. Cancel it — cancel_background_task with reason: 'Wrong parameters'\n"
            "4. Run 'sleep 45 && echo ATTEMPT_2' in background (background: true)\n"
            "5. Check progress using check_background_progress\n"
            "6. Cancel it too — cancel_background_task with reason: 'Still wrong'\n"
            "7. Run 'sleep 5 && echo ATTEMPT_3_DONE' in background (background: true)\n"
            "8. Run 'echo FG_WHILE_WAITING' in foreground\n"
            "9. Check progress on attempt 3 using check_background_progress\n"
            "10. Report: how many attempts were made and what's the final status?\n\n"
            "Use background: true for steps 1, 4, and 7."
        )
        _log_result(result, "rapid_fire")

        assert len(result.background_started()) >= 3, \
            f"Expected 3 background launches. Got {len(result.background_started())}"
        cancels = result.tool_count("cancel_background_task")
        assert cancels >= 2, \
            f"Expected 2+ cancels. Got {cancels}"
        checks = result.tool_count("check_background_progress") + result.tool_count("wait_for_background_task")
        assert checks >= 3, \
            f"Expected 3+ progress/wait checks. Got {checks}"


# ===========================================================================
# Test 5: Notification-driven workflow — act on bg completion
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestNotificationDrivenWorkflow:
    """LLM acts on background task completion to trigger next phase."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("chaos-notify")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_bg_completion_triggers_next_phase(self, sandbox):
        """Short bg task completes, LLM uses result to drive next actions."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Execute this multi-phase workflow:\n\n"
            "Phase 1:\n"
            "1. Run 'sleep 3 && echo PHASE1_BUILD_OK' in background (background: true)\n"
            "2. Run 'echo PHASE1_LINT_OK' in foreground\n"
            "3. Check background progress — wait until phase 1 build is done\n\n"
            "Phase 2 (only after phase 1 bg completes):\n"
            "4. Create /home/daytona/build_artifact.txt with 'build_v1_hash_abc123' "
            "using daytona_write_file\n"
            "5. Run 'sleep 3 && echo PHASE2_TESTS_OK' in background (background: true)\n"
            "6. Run 'echo PHASE2_INTEGRATION_OK' in foreground\n"
            "7. Check background progress on phase 2 task\n\n"
            "Phase 3 (final):\n"
            "8. Create /home/daytona/deploy_manifest.txt with 'deploy_ready: true' "
            "using daytona_write_file\n"
            "9. Run 'cat /home/daytona/build_artifact.txt && cat /home/daytona/deploy_manifest.txt' "
            "in foreground\n"
            "10. Report the full pipeline result\n\n"
            "Use background: true for steps 1 and 5 ONLY."
        )
        _log_result(result, "notify_workflow")

        assert len(result.background_started()) >= 2, \
            f"Expected 2 background phases. Got {len(result.background_started())}"
        checks = result.tool_count("check_background_progress") + result.tool_count("wait_for_background_task")
        assert checks >= 2, \
            f"Expected 2+ progress/wait checks. Got {checks}"
        write_count = result.tool_count("daytona_write_file")
        assert write_count >= 2, \
            f"Expected 2+ file writes. Got {write_count}"
        assert len(result.tools_started()) >= 8, \
            f"Expected 8+ tool calls for full pipeline. Got {len(result.tools_started())}"
        assert not result.has_unrecovered_errors, \
            f"Pipeline errors: {[e.output[:200] for e in result.unrecovered_error_events]}"
