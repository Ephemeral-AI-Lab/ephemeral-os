# ruff: noqa
"""Live E2E: Complex idle and wait patterns with background tasks.

Tests that the LLM correctly handles various idle situations — entering wait mode,
using periodic check-ins, making timeout-based decisions, and managing transitions
between active work and idle monitoring phases.

Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_bg_idle_patterns_live.py -v -s --log-cli-level=INFO
"""
from __future__ import annotations
import logging
import pytest
from engine.testing.eval_agent import EvalAgent
from message.stream_events import ToolExecutionCompleted
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are test-idle-agent, a developer with a remote Daytona sandbox.

IMPORTANT RULES:
- You MUST use tools for every action — never just describe what you'd do.
- Use daytona_bash to run commands, daytona_write_file to create files.
- You have background task support: add "background": true to tool input for long-running operations.
- Use check_background_progress for instant status snapshots.
- Use wait_for_background_task to block when you have no foreground work left.
- Use cancel_background_task to cancel tasks.

IDLE AND WAIT STRATEGY:
- When you have foreground work, do it while background runs.
- When foreground is exhausted, transition to idle monitoring:
  1. Call check_background_progress first
  2. Then use wait_for_background_task to block efficiently
- Use short timeouts (3-5s) for periodic check-ins on long tasks.
- Use longer timeouts (10-15s) when you expect tasks to finish soon.
- Cancel tasks that exceed reasonable time limits.

Always be concise. Execute tools, don't just describe them.
"""


def _log_result(result, label: str) -> None:
    checks = result.tool_count("check_background_progress")
    cancels = result.tool_count("cancel_background_task")
    waits = result.tool_count("wait_for_background_task")

    logger.info(
        f"\n{'='*60}\n[{label}] Idle/Wait summary:\n"
        f"  Tools started: {len(result.tools_started())}\n"
        f"  Background started: {len(result.background_started())}\n"
        f"  Background completed: {len(result.background_completed())}\n"
        f"  Progress checks: {checks}\n"
        f"  Wait calls: {waits}\n"
        f"  Cancels: {cancels}\n"
        f"  Tool sequence: {result.tool_names}\n"
        f"{'='*60}"
    )


# ===========================================================================
# Test 1: Transition from foreground work to background wait
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestIdleTransitionFromFgToBgWait:
    """LLM completes foreground work then transitions into idle wait mode."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-transition")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_fg_to_idle_wait_transition(self, sandbox):
        """Do foreground tasks, then enter idle wait when all fg work is exhausted."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Do these steps in order:\n"
            "1. Launch 'sleep 8 && echo BG_READY' in background (background: true)\n"
            "2. Do foreground work: 'echo TASK_1', 'echo TASK_2', 'echo TASK_3'\n"
            "3. All foreground done. Now enter idle monitoring:\n"
            "4. Call check_background_progress — task should still be running\n"
            "5. Call wait_for_background_task with timeout=12 to block until it finishes\n"
            "6. Report: foreground tasks completed, then waited for bg, final result"
        )
        _log_result(result, "fg_to_idle_wait")

        # 1 background launch
        bg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and tc.input.get("background") is True]
        assert len(bg_bash) >= 1, \
            f"Expected 1+ background launch. Got {len(bg_bash)}"
        assert len(result.background_started()) >= 1, \
            f"Expected BackgroundTaskStarted event. Got: {result.tool_names}"

        # fg bash >= 3
        fg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and not tc.input.get("background")]
        assert len(fg_bash) >= 3, \
            f"Expected 3+ foreground bash calls (TASK_1/2/3). Got {len(fg_bash)}"

        # has check_background_progress and wait_for_background_task
        assert result.has_tool("check_background_progress"), \
            f"Expected check_background_progress. Got: {result.tool_names}"
        assert result.has_tool("wait_for_background_task"), \
            f"Expected wait_for_background_task. Got: {result.tool_names}"

        # check index < wait index
        check_indices = [i for i, tc in enumerate(result.tool_calls)
                         if tc.name == "check_background_progress"]
        wait_indices = [i for i, tc in enumerate(result.tool_calls)
                        if tc.name == "wait_for_background_task"]
        assert check_indices[0] < wait_indices[0], \
            f"check_background_progress must precede wait_for_background_task. " \
            f"checks={check_indices}, waits={wait_indices}"

        # text mentions "BG_READY" or "complet"
        text_lower = result.text.lower()
        assert any(w in text_lower for w in ["bg_ready", "complet", "finish", "done"]), \
            f"Expected LLM to mention BG_READY or completion. Got: {result.text[:300]}"

        # no non_cancel_errors
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 2: Periodic polling with short timeouts
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestIdlePeriodicPolling:
    """LLM uses repeated short-timeout waits to periodically check progress."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-periodic")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_periodic_wait_timeout_check_cycle(self, sandbox):
        """Use multiple short-timeout waits to poll a long-running background task."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 'sleep 10 && echo PERIODIC_DONE' in background. Do 'echo START'.\n"
            "Now do a periodic monitoring cycle:\n"
            "1. Check progress\n"
            "2. Wait with timeout=3 — will timeout (task takes 10s)\n"
            "3. Check progress again — note elapsed time increased\n"
            "4. Wait with timeout=3 — might still timeout\n"
            "5. Wait with timeout=10 — should complete this time\n"
            "Report: how many wait attempts, when it finally completed."
        )
        _log_result(result, "periodic_polling")

        # tool_count("wait_for_background_task") >= 2
        wait_count = result.tool_count("wait_for_background_task")
        assert wait_count >= 2, \
            f"Expected 2+ wait_for_background_task calls (multiple attempts). Got {wait_count}"

        # tool_count("check_background_progress") >= 2
        check_count = result.tool_count("check_background_progress")
        assert check_count >= 2, \
            f"Expected 2+ check_background_progress calls. Got {check_count}"

        # text contains "PERIODIC_DONE" or "complet"
        text_lower = result.text.lower()
        assert any(w in text_lower for w in ["periodic_done", "complet", "finish", "done"]), \
            f"Expected LLM to mention PERIODIC_DONE or completion. Got: {result.text[:300]}"

        # no non_cancel_errors
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 3: Pure wait — no foreground work at all
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestIdleNoFgWorkPureWait:
    """No foreground work — LLM launches two bg tasks and manages them purely in idle mode."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-pure-wait")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_pure_idle_no_fg_just_wait(self, sandbox):
        """Launch two bg tasks, wait for first to complete, cancel second. No fg work."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "There is NO foreground work. Only background monitoring:\n"
            "1. Launch 'sleep 4 && echo PURE_WAIT_DONE' in background (background: true)\n"
            "2. Launch 'sleep 30 && echo SLOW_TASK' in background (background: true)\n"
            "3. Call check_background_progress\n"
            "4. Call wait_for_background_task with timeout=10 — first task should complete\n"
            "5. Check progress — first done, second still running\n"
            "6. Cancel the slow task with reason 'No longer needed'\n"
            "7. Report both task outcomes"
        )
        _log_result(result, "pure_idle_wait")

        # 2 background launches
        bg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and tc.input.get("background") is True]
        assert len(bg_bash) >= 2, \
            f"Expected 2+ background launches. Got {len(bg_bash)}"
        assert len(result.background_started()) >= 2, \
            f"Expected 2 BackgroundTaskStarted events. Got {len(result.background_started())}"

        # fg bash == 0 (no foreground bash work)
        fg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and not tc.input.get("background")]
        assert len(fg_bash) == 0, \
            f"Expected NO foreground bash calls (pure idle mode). Got {len(fg_bash)}: {[tc.input for tc in fg_bash]}"

        # has check_background_progress, wait_for_background_task, cancel_background_task
        assert result.has_tool("check_background_progress"), \
            f"Expected check_background_progress. Got: {result.tool_names}"
        assert result.has_tool("wait_for_background_task"), \
            f"Expected wait_for_background_task. Got: {result.tool_names}"
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel_background_task. Got: {result.tool_names}"

        # text mentions completion and cancellation
        text_lower = result.text.lower()
        assert any(w in text_lower for w in ["pure_wait_done", "complet", "finish", "done"]), \
            f"Expected mention of first task completion. Got: {result.text[:300]}"
        assert any(w in text_lower for w in ["cancel", "no longer needed", "slow"]), \
            f"Expected mention of cancellation. Got: {result.text[:300]}"

        # no non_cancel_errors
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 4: Wait then resume foreground work
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestIdleWaitThenResumeFg:
    """LLM waits for background task, then uses its result to drive foreground work."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-resume")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_wait_completes_then_resume_fg_work(self, sandbox):
        """Phase: bg + fg prep -> idle wait -> resume fg based on bg result."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Phase 1 — Background + foreground:\n"
            "1. Launch 'sleep 4 && echo CONFIG_GENERATED' in background\n"
            "2. Do 'echo PREPARING_ENV'\n"
            "Phase 2 — Idle wait:\n"
            "3. Check progress, then wait with timeout=10\n"
            "Phase 3 — Resume foreground based on bg result:\n"
            "4. The bg task output says CONFIG_GENERATED. Now create /home/daytona/config.json "
            "with content '{\"ready\": true}' using daytona_write_file\n"
            "5. Run 'cat /home/daytona/config.json' to verify\n"
            "Report the three phases."
        )
        _log_result(result, "wait_then_resume_fg")

        # 1 background launch
        bg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and tc.input.get("background") is True]
        assert len(bg_bash) >= 1, \
            f"Expected 1+ background launch. Got {len(bg_bash)}"
        assert len(result.background_started()) >= 1, \
            f"Expected BackgroundTaskStarted event. Got: {result.tool_names}"

        # has wait_for_background_task
        assert result.has_tool("wait_for_background_task"), \
            f"Expected wait_for_background_task. Got: {result.tool_names}"

        # has daytona_write_file with "config.json" in file_path
        write_calls = [tc for tc in result.tool_calls if tc.name == "daytona_write_file"]
        assert len(write_calls) >= 1, \
            f"Expected daytona_write_file. Got: {result.tool_names}"
        assert any("config.json" in tc.input.get("file_path", "") for tc in write_calls), \
            f"Expected config.json write. Got paths: {[tc.input.get('file_path') for tc in write_calls]}"

        # wait index < write index (waited before resuming fg)
        wait_indices = [i for i, tc in enumerate(result.tool_calls)
                        if tc.name == "wait_for_background_task"]
        write_indices = [i for i, tc in enumerate(result.tool_calls)
                         if tc.name == "daytona_write_file"]
        assert wait_indices[0] < write_indices[0], \
            f"wait_for_background_task must precede daytona_write_file. " \
            f"waits={wait_indices}, writes={write_indices}"

        # fg bash includes "cat" and "config.json"
        fg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and not tc.input.get("background")]
        assert any("cat" in str(tc.input) and "config.json" in str(tc.input) for tc in fg_bash), \
            f"Expected 'cat config.json' verification. Got fg calls: {[tc.input for tc in fg_bash]}"

        # write tool itself must not error; cat failures are tolerated (sandbox write latency)
        write_errors = [
            e for e in result.non_cancel_error_events
            if isinstance(e, ToolExecutionCompleted) and e.tool_name == "daytona_write_file"
        ]
        assert not write_errors, \
            f"daytona_write_file failed: {[e.output[:200] for e in write_errors]}"


# ===========================================================================
# Test 5: Escalating timeout strategy
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestIdleEscalatingTimeout:
    """LLM uses escalating wait timeouts to efficiently monitor a long background task."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-escalate")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_escalating_timeout_strategy(self, sandbox):
        """Use increasing timeout values across multiple wait attempts."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 'sleep 18 && echo ESCALATED_DONE' in background. Do 'echo MONITOR'.\n"
            "You MUST call wait_for_background_task EXACTLY 3 times with escalating timeouts. "
            "Do NOT skip any step:\n"
            "1. check_background_progress\n"
            "2. wait_for_background_task timeout=3 — this WILL timeout (task takes 18s)\n"
            "3. check_background_progress — note elapsed time\n"
            "4. wait_for_background_task timeout=5 — this WILL timeout again\n"
            "5. check_background_progress — note elapsed time increasing\n"
            "6. wait_for_background_task timeout=15 — should finally complete\n"
            "Report: each timeout attempt and when it finally succeeded."
        )
        _log_result(result, "escalating_timeout")

        # tool_count("wait_for_background_task") >= 3
        wait_count = result.tool_count("wait_for_background_task")
        assert wait_count >= 3, \
            f"Expected 3+ wait_for_background_task calls (escalating). Got {wait_count}"

        # tool_count("check_background_progress") >= 2
        check_count = result.tool_count("check_background_progress")
        assert check_count >= 2, \
            f"Expected 2+ check_background_progress calls. Got {check_count}"

        # text contains "ESCALATED_DONE" or "complet"
        text_lower = result.text.lower()
        assert any(w in text_lower for w in ["escalated_done", "complet", "finish", "done", "success"]), \
            f"Expected LLM to mention ESCALATED_DONE or completion. Got: {result.text[:300]}"

        # no non_cancel_errors
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"


# ===========================================================================
# Test 6: Multiple staggered background tasks with pure idle monitoring
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestIdleMultipleBgStaggeredWait:
    """Three staggered background tasks — wait for each in order, cancel the last."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("idle-multi-stagger")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_idle_wait_staggered_multiple_bg(self, sandbox):
        """Launch 3 staggered bg tasks, wait for first two, cancel the third."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Launch 3 background tasks with staggered durations:\n"
            "1. 'sleep 3 && echo ALPHA_DONE' (background: true)\n"
            "2. 'sleep 6 && echo BETA_DONE' (background: true)\n"
            "3. 'sleep 45 && echo GAMMA_DONE' (background: true)\n"
            "No foreground work — pure idle monitoring:\n"
            "1. Check progress — all 3 running\n"
            "2. Wait for any (timeout=8) — ALPHA should finish first\n"
            "3. Check progress — ALPHA done, BETA/GAMMA running\n"
            "4. Wait for any (timeout=8) — BETA should finish\n"
            "5. Check progress — ALPHA/BETA done, GAMMA still running\n"
            "6. Cancel GAMMA with reason 'Taking too long'\n"
            "7. Report: completion order and final states"
        )
        _log_result(result, "staggered_multi_bg")

        # 3 background launches
        bg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and tc.input.get("background") is True]
        assert len(bg_bash) >= 3, \
            f"Expected 3+ background launches. Got {len(bg_bash)}"
        assert len(result.background_started()) >= 3, \
            f"Expected 3 BackgroundTaskStarted events. Got {len(result.background_started())}"

        # fg bash == 0 (pure idle)
        fg_bash = [tc for tc in result.tool_calls
                   if tc.name == "daytona_bash" and not tc.input.get("background")]
        assert len(fg_bash) == 0, \
            f"Expected NO foreground bash calls (pure idle mode). Got {len(fg_bash)}: {[tc.input for tc in fg_bash]}"

        # tool_count("wait_for_background_task") >= 2
        wait_count = result.tool_count("wait_for_background_task")
        assert wait_count >= 2, \
            f"Expected 2+ wait_for_background_task calls. Got {wait_count}"

        # tool_count("check_background_progress") >= 3
        check_count = result.tool_count("check_background_progress")
        assert check_count >= 3, \
            f"Expected 3+ check_background_progress calls. Got {check_count}"

        # has cancel_background_task — cancelled GAMMA
        assert result.has_tool("cancel_background_task"), \
            f"Expected cancel_background_task (GAMMA). Got: {result.tool_names}"

        cancel_calls = [tc for tc in result.tool_calls if tc.name == "cancel_background_task"]
        assert cancel_calls[0].input.get("reason"), \
            f"Expected cancel with reason. Got: {cancel_calls[0].input}"

        # text mentions completion of first two and cancellation of third
        text_lower = result.text.lower()
        assert any(w in text_lower for w in ["alpha", "alpha_done", "first"]), \
            f"Expected mention of ALPHA completion. Got: {result.text[:300]}"
        assert any(w in text_lower for w in ["beta", "beta_done", "second"]), \
            f"Expected mention of BETA completion. Got: {result.text[:300]}"
        assert any(w in text_lower for w in ["cancel", "gamma", "too long"]), \
            f"Expected mention of GAMMA cancellation. Got: {result.text[:300]}"

        # no non_cancel_errors
        assert not result.has_non_cancel_errors, \
            f"Unexpected errors: {[e.output[:200] for e in result.non_cancel_error_events]}"
