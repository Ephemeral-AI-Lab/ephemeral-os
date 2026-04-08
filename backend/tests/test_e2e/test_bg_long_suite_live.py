# ruff: noqa
"""Live E2E: Long-running suite with autonomous early cancellation.

The most complex supernova scenario: the agent runs a ~150-second integration
test suite that keeps running even after fatal errors (buggy test harness).
The agent must:
  - Launch the suite in background
  - Wait with short timeouts to avoid burning full runtime
  - Read the log file via foreground cat to see partial progress
  - Detect [FATAL] markers and cancel the background task early to save time
  - Diagnose the bug from the log content
  - Update the config
  - Repeat until the full suite passes (3 bugs, 3 iterations minimum)

No workflow guidance is given in the prompt — the agent discovers the pattern.

Run with: .venv/bin/python -m pytest backend/tests/test_e2e/test_bg_long_suite_live.py -v -s --log-cli-level=INFO
"""
from __future__ import annotations

import json
import logging
import shlex
import textwrap

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

AGENT_PROMPT = """\
You are a senior developer with a remote Daytona sandbox.

You MUST use tools for every action. Never describe what you'd do — execute it.
Use whichever tools are appropriate for the task.

For long-running commands (tests, builds), run them in background with "background": true,
then use wait_for_background_task to block for the final result.

You also have check_background_progress, which is non-blocking and returns a
LIVE TAIL of the stdout that the background command has emitted so far. Prefer
this over polling external log files — it shows the real stream from the task
as it runs. Use last_n_lines to limit how much you fetch.

Make autonomous decisions from the live tail:
  * If you see a clearly fatal marker (e.g. [FATAL], FAIL, traceback, "STAGE
    FAILED", "CANNOT", "wrong"), CANCEL the task immediately with
    cancel_background_task instead of waiting for the full timeout.
  * If progress looks healthy, keep waiting.

You are an autonomous agent. Analyze failures, reason about root causes, apply fixes,
and verify your fixes work. Keep iterating until the problem is solved.
"""


def _log_result(result, label: str) -> None:
    waits = result.tool_count("wait_for_background_task")
    checks = result.tool_count("check_background_progress")
    cancels = result.tool_count("cancel_background_task")
    bg_started = len(result.background_started())
    bg_completed = len(result.background_completed())

    logger.info(
        f"\n{'='*60}\n[{label}] Long-suite summary:\n"
        f"  Tools used: {len(result.tool_calls)}\n"
        f"  Background started: {bg_started}\n"
        f"  Background completed: {bg_completed}\n"
        f"  Progress checks: {checks}\n"
        f"  Wait calls: {waits}\n"
        f"  Cancel calls: {cancels}\n"
        f"  Tool sequence: {result.tool_names}\n"
        f"{'='*60}"
    )


def _verify_suite_passes(
    sandbox_id: str, command: str, marker: str, timeout: int = 300
) -> tuple[bool, str]:
    """Run the suite in the sandbox and check for a success marker.

    This is the ground truth — run after the agent is done to confirm fixes work.
    """
    from sandbox.testing import get_sandbox_service

    svc = get_sandbox_service()
    sb = svc.get_sandbox_object(sandbox_id)
    log_path = "/tmp/eos_long_suite_verify.log"
    wrapped = (
        f"{command} > {log_path} 2>&1; "
        "code=$?; "
        f"tail -n 120 {log_path}; "
        "exit $code"
    )
    resp = sb.process.exec(f"bash -lc {shlex.quote(wrapped)}", timeout=timeout)
    output = getattr(resp, "result", "") or getattr(resp, "stdout", "") or ""
    exit_code = getattr(resp, "exit_code", None)
    if marker in output and (exit_code == 0 or exit_code is None):
        return True, output
    if "[FATAL]" not in output and "FAIL" not in output and "e2e_09" in output:
        return True, output
    return False, output


# ===========================================================================
# Long-running integration suite (~150s) with three cascading config bugs.
# Keeps running after FATAL errors so external cancel is required to save time.
# ===========================================================================


LONG_SUITE_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env python3
    \"\"\"Long integration test suite — up to ~150 seconds if all phases run.

    Streams incremental progress to stdout. Keeps running even after fatal
    errors — an external cancel is needed to save time when FATAL markers
    appear.
    \"\"\"
    import json
    import sys
    import time

    CONFIG = "/home/daytona/long_suite/config.json"

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception as e:
        log(f"[FATAL] Cannot read config: {e}")
        sys.exit(2)

    log("[START] Integration suite starting")
    log(f"[CONFIG] env={cfg.get('env')} region={cfg.get('region')} "
        f"api_key_prefix={cfg.get('api_key', '')[:8]} "
        f"timeout_ms={cfg.get('timeout_ms')}")

    FATAL = False  # once set, remaining tests are skipped but suite runs on

    # Phase 1: Setup — ~10 seconds
    log("")
    log("[PHASE 1/4] Setup (takes ~10s)")
    time.sleep(2)
    log("  - Loading secrets")
    time.sleep(2)
    api_key = cfg.get("api_key", "")
    if not api_key.startswith("sk_live_"):
        # BUG 1: wrong api_key prefix — fatal for everything downstream
        log(f"  - [FATAL] API key has wrong prefix: '{api_key[:8]}' (expected 'sk_live_')")
        log("  - [FATAL] Cannot authenticate with upstream service")
        log("  - [FATAL] All subsequent phases will be skipped, but suite will still run to completion")
        FATAL = True
    else:
        log("  - Secrets loaded: OK")
    time.sleep(2)
    log("  - Connecting to infrastructure")
    time.sleep(2)
    log("  - Infrastructure ready: OK")
    time.sleep(2)
    log("[PHASE 1/4] Setup complete")

    # Phase 2: Unit tests — ~40 seconds
    log("")
    log("[PHASE 2/4] Unit tests (takes ~40s, 20 tests)")
    for i in range(1, 21):
        time.sleep(2)
        if FATAL:
            log(f"  - test_unit_{i:02d}: SKIP (prior fatal)")
            continue
        timeout_ms = cfg.get("timeout_ms", 1000)
        if i == 7 and timeout_ms < 5000:
            # BUG 2: timeout too low — fatal
            log(f"  - test_unit_{i:02d}: FAIL — exceeded {timeout_ms}ms timeout")
            log(f"  - [FATAL] Timeout config {timeout_ms}ms too low (need >= 5000)")
            FATAL = True
            continue
        log(f"  - test_unit_{i:02d}: PASS")

    # Phase 3: Integration tests — ~50 seconds
    log("")
    log("[PHASE 3/4] Integration tests (takes ~50s, 10 scenarios)")
    for i in range(1, 11):
        time.sleep(5)
        if FATAL:
            log(f"  - integration_{i:02d}: SKIP (prior fatal)")
            continue
        if i == 3 and cfg.get("region") != "us-east-1":
            # BUG 3: wrong region — fatal
            log(f"  - integration_{i:02d}: FAIL — data not available in region "
                f"'{cfg.get('region')}' (only available in 'us-east-1')")
            log(f"  - [FATAL] Wrong region configured")
            FATAL = True
            continue
        log(f"  - integration_{i:02d}: PASS")

    # Phase 4: E2E tests — ~50 seconds
    log("")
    log("[PHASE 4/4] E2E tests (takes ~50s, 10 journeys)")
    for i in range(1, 11):
        time.sleep(5)
        if FATAL:
            log(f"  - e2e_{i:02d}: SKIP (prior fatal)")
            continue
        log(f"  - e2e_{i:02d}: PASS")

    log("")
    if FATAL:
        log("[RESULT] SUITE FAILED — see [FATAL] markers above")
        sys.exit(1)

    log("=" * 50)
    log("INTEGRATION SUITE: ALL PHASES PASSED")
    log("=" * 50)
    sys.exit(0)
""")

LONG_SUITE_INITIAL_CONFIG = json.dumps({
    "env": "production",
    "region": "us-west-2",       # BUG 3
    "api_key": "pk_test_abc123", # BUG 1 — wrong prefix
    "timeout_ms": 1000,          # BUG 2 — too low
}, indent=2)


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestLongSuiteEarlyCancel:
    """Agent runs a 150s integration suite, detects fatal errors from logs, cancels early.

    The suite keeps running even after fatal errors — the agent must detect
    this from the log file and cancel to avoid wasting ~2 minutes per bug.
    Three bugs must be fixed sequentially, each revealed by the previous fix.
    """

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("long-suite")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture(scope="class", autouse=True)
    def seed_files(self, sandbox):
        """Pre-populate sandbox with the long test suite and buggy config."""
        from sandbox.testing import get_sandbox_service

        svc = get_sandbox_service()
        sb = svc.get_sandbox_object(sandbox["id"])
        sb.process.exec("mkdir -p /home/daytona/long_suite")
        sb.fs.upload_file(
            LONG_SUITE_SCRIPT.encode(), "/home/daytona/long_suite/run_suite.py"
        )
        sb.fs.upload_file(
            LONG_SUITE_INITIAL_CONFIG.encode(), "/home/daytona/long_suite/config.json"
        )
        sb.process.exec("chmod +x /home/daytona/long_suite/run_suite.py")

    @pytest.mark.asyncio
    async def test_autonomous_long_suite_early_cancel_iterations(self, sandbox):
        """Agent must iterate on a slow failing suite, cancelling early each round."""
        agent = create_eval_agent(
            system_prompt=AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
            max_turns=400,
        )
        result = await agent.invoke(
            "There is an integration test suite at /home/daytona/long_suite/ with:\n"
            "- run_suite.py — a long integration test suite (TREAT AS A BLACK BOX)\n"
            "- config.json — configuration the suite reads\n\n"
            "RULES:\n"
            "- DO NOT read run_suite.py — treat it as an opaque black box you must\n"
            "  learn about purely from its runtime output. You may read config.json.\n"
            "- You must discover bugs by RUNNING the suite and OBSERVING its live\n"
            "  stdout via check_background_progress, not by static inspection.\n\n"
            "The full suite takes about 150 seconds when everything works, and it "
            "keeps running to completion even after fatal errors.\n\n"
            "The suite is currently failing. Make it pass. You have a limited time "
            "budget — don't waste it waiting for runs you can already tell will fail "
            "from the partial output.\n\n"
            "IMPORTANT: You MUST run the suite as a background task (set "
            '"background": true on the bash tool call). The PREFERRED way to peek '
            "at partial output is check_background_progress(task_id=..., "
            "last_n_lines=30) — that returns the live tail of the suite's stdout "
            "directly from the running task, no log file needed. Use it to detect "
            "[FATAL] markers as soon as they appear, then cancel_background_task "
            "to save time, fix the config, and relaunch. Do this for every suite "
            "run.\n\n"
            "NEVER use `sleep` in a foreground bash command to wait for the "
            "suite. Do not run things like `sleep 60 && tail ...` — that blocks "
            "your turn and defeats the whole background workflow. The only "
            "acceptable ways to pass time are: (1) wait_for_background_task with "
            "a short timeout, (2) check_background_progress (live tail, "
            "non-blocking)."
        )
        _log_result(result, "long_suite_cancel")

        # Behavioral check: the agent must exercise the background workflow —
        # without backgrounding, there's no way to read the live log while the
        # suite runs, and the "detect fatal early, cancel, save time" pattern
        # that this test exists to validate cannot happen.
        assert len(result.background_started()) >= 1, (
            "Expected agent to launch the suite as a background task at least once "
            "so it could observe the live tail via check_background_progress. "
            f"Got {len(result.background_started())} background launches. "
            f"Tool sequence: {result.tool_names}"
        )

        # Live-tail behavioral check: the agent must have actually exercised
        # the streaming feature — calling check_background_progress on a
        # background task that was still running, and observing live stdout
        # in the response. This proves the agent used the new streaming path
        # rather than just polling external log files or waiting blind.
        assert result.has_tool("check_background_progress"), (
            f"Agent never called check_background_progress. "
            f"Tool sequence: {result.tool_names}"
        )
        check_completions = [
            e for e in result.tools_completed()
            if e.tool_name == "check_background_progress"
        ]
        saw_live_tail = any(
            '"status": "running"' in (e.output or "")
            and '"output"' in (e.output or "")
            for e in check_completions
        )
        assert saw_live_tail, (
            "No mid-flight check_background_progress call surfaced a live stdout "
            "tail (status=running with an output field). The agent did not exercise "
            "the streaming feature on a still-running task. Outputs: "
            f"{[(e.output or '')[:300] for e in check_completions]}"
        )

        # Ground truth: run the suite ourselves and verify it passes end-to-end
        passed, output = _verify_suite_passes(
            sandbox["id"],
            "cd /home/daytona/long_suite && python3 run_suite.py",
            "INTEGRATION SUITE: ALL PHASES PASSED",
        )
        assert passed, (
            "Ground-truth suite re-run did not pass. "
            f"Last 2000 chars:\n{output[-2000:]}"
        )
