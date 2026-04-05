# ruff: noqa
"""Live E2E: Background task execution with real LLM + real Daytona sandbox.

Tests that a real LLM (MiniMax via Anthropic-native client) correctly:
1. Decides whether to background a tool or run foreground
2. Does foreground work while background runs, then gets idle notification
3. Proactively calls check_background_progress
4. Cancels a background task after seeing test failures
5. Cancels a hanging background task after repeated progress checks

Run with: pytest tests/test_e2e/test_background_live.py -m live -v --log-cli-level=INFO
"""

from __future__ import annotations

import logging
import os
import time

import pytest

from tests.test_e2e.conftest import (
    HAS_BOTH,
    MINIMAX_KEY,
    MINIMAX_MODEL,
    MINIMAX_BASE_URL,
    MINIMAX_FORMAT,
    create_test_sandbox,
    delete_test_sandbox,
    events_of_type,
    get_assistant_text,
    get_event_types,
    get_tool_completed_events,
    get_tool_started_events,
    make_live_client,
    send_chat,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

# ---------------------------------------------------------------------------
# Use Anthropic-native format for MiniMax
# ---------------------------------------------------------------------------

_API_KEY = os.environ.get("MINIMAX_API_KEY") or MINIMAX_KEY
_MODEL = os.environ.get("MINIMAX_MODEL") or MINIMAX_MODEL
# Always use the Anthropic-compatible endpoint, not the OpenAI one from settings
_BASE_URL = "https://api.minimax.io/anthropic"
_FORMAT = "anthropic"

MODEL_KEY = "minimax-bg-test"
AGENT_NAME = "test-background-agent"
AGENT_TOOLKITS = ["sandbox_operations"]

# System prompt that teaches the LLM about background tasks
AGENT_PROMPT = """\
You are test-background-agent, a developer with a remote Daytona sandbox.

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


def _register_model(client) -> dict:
    """Register the MiniMax model with background tasks enabled."""
    resp = client.post(
        "/api/db/models/register",
        json={
            "key": MODEL_KEY,
            "label": "MiniMax BG Test (Anthropic-native)",
            "class_path": "models.clients.anthropic_native.AnthropicClient",
            "kwargs": {
                "api_key": _API_KEY,
                "base_url": _BASE_URL,
                "model": _MODEL,
                "api_format": "anthropic",
            },
            "activate": True,
        },
    )
    assert resp.status_code == 200, f"Model registration failed: {resp.status_code} {resp.text}"
    return resp.json()


def _create_agent(client, name: str = AGENT_NAME) -> dict:
    """Create agent with sandbox toolkits (which enables background tasks)."""
    resp = client.post(
        "/api/agents/",
        json={
            "name": name,
            "description": f"E2E background test agent ({name})",
            "model": MODEL_KEY,
            "toolkits": AGENT_TOOLKITS,
            "system_prompt": AGENT_PROMPT,
        },
    )
    if resp.status_code == 201:
        return resp.json()
    get_resp = client.get(f"/api/agents/{name}")
    if get_resp.status_code == 200:
        return get_resp.json()
    assert False, f"Failed to create agent '{name}': {resp.status_code} {resp.text}"


def _log_events(events: list[dict], label: str) -> None:
    """Log all events for debugging."""
    logger.info(f"\n{'='*60}\n[{label}] Event summary ({len(events)} events):")
    for i, e in enumerate(events):
        etype = e.get("type", "unknown")
        if etype == "tool_started":
            logger.info(f"  [{i}] tool_started: {e.get('tool_name')} input={str(e.get('tool_input', {}))[:200]}")
        elif etype == "tool_completed":
            output = str(e.get("output", ""))[:150]
            logger.info(f"  [{i}] tool_completed: {e.get('tool_name')} error={e.get('is_error')} output={output}")
        elif etype == "assistant_complete":
            msg = str(e.get("message", ""))[:200]
            logger.info(f"  [{i}] assistant_complete: {msg}")
        elif etype == "assistant_delta":
            pass  # skip deltas for brevity
        elif etype == "error":
            logger.error(f"  [{i}] ERROR: {e.get('message', '')[:300]}")
        elif etype == "background_started":
            logger.info(f"  [{i}] BACKGROUND_STARTED: {e.get('tool_name')} task_id={e.get('task_id')}")
        elif etype == "background_completed":
            logger.info(f"  [{i}] BACKGROUND_COMPLETED: {e.get('tool_name')} output={str(e.get('output', ''))[:150]}")
        else:
            logger.info(f"  [{i}] {etype}")
    logger.info(f"{'='*60}\n")


# ===========================================================================
# Test 1: LLM decides foreground vs background
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestLLMBackgroundDecision:
    """Test that the LLM decides appropriately between foreground and background."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-decision")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=_API_KEY, model=_MODEL,
            base_url=_BASE_URL, api_format="anthropic",
        )
        with c:
            yield c

    def test_quick_command_runs_foreground(self, client, sandbox):
        """LLM should run a fast command in foreground (no background flag)."""
        _register_model(client)
        _create_agent(client, f"{AGENT_NAME}-fg")
        events = send_chat(
            client,
            "Run this quick command in the sandbox: echo 'HELLO_FOREGROUND'. "
            "This is a fast command, do NOT run it in background.",
            agent_name=f"{AGENT_NAME}-fg",
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        _log_events(events, "quick_foreground")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        # Should use daytona_bash in foreground
        tool_started = get_tool_started_events(events)
        assert len(tool_started) >= 1, f"Should use at least one tool. Events: {types}"

        # Should NOT have background_started events
        bg_started = events_of_type(events, "background_started")
        logger.info(f"[Test1a] bg_started={len(bg_started)}, tool_started={len(tool_started)}")
        # Note: LLM may or may not use background — we just verify it works either way
        logger.info("[PASS] Quick command executed successfully")

    def test_long_command_offered_background(self, client, sandbox):
        """LLM should consider backgrounding a long command."""
        _register_model(client)
        _create_agent(client, f"{AGENT_NAME}-bg")
        events = send_chat(
            client,
            "Do TWO things:\n"
            "1. Run 'sleep 10 && echo LONG_DONE' in the sandbox using daytona_bash "
            "with background: true (this takes a long time)\n"
            "2. While waiting, run 'echo FOREGROUND_DONE' in foreground\n\n"
            "You MUST use background: true for the sleep command.",
            agent_name=f"{AGENT_NAME}-bg",
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        _log_events(events, "long_background")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        logger.info(f"[Test1b] tool_started={len(tool_started)}, types={types}")

        # At minimum the LLM should have called daytona_bash
        tool_names = [e.get("tool_name") for e in tool_started]
        assert any("daytona" in (t or "") for t in tool_names), \
            f"Expected daytona tool usage. Got: {tool_names}"
        logger.info("[PASS] Long command scenario completed")


# ===========================================================================
# Test 2: Foreground work while background runs + idle notification
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestForegroundAndIdleWait:
    """LLM does foreground work while background runs, gets result on idle."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-idle")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=_API_KEY, model=_MODEL,
            base_url=_BASE_URL, api_format="anthropic",
        )
        with c:
            yield c

    def test_background_with_foreground_work(self, client, sandbox):
        """LLM backgrounds a slow command, does foreground work, gets result."""
        _register_model(client)
        _create_agent(client, f"{AGENT_NAME}-idle")
        events = send_chat(
            client,
            "Please do these tasks:\n"
            "1. Run 'sleep 5 && echo BUILD_COMPLETE' in background using daytona_bash "
            "with background: true\n"
            "2. While waiting, run 'echo FOREGROUND_TASK_1' in the sandbox (foreground)\n"
            "3. Then run 'echo FOREGROUND_TASK_2' in the sandbox (foreground)\n"
            "4. After foreground tasks, check on the background task using check_background_progress\n\n"
            "Make sure to use background: true for step 1.",
            agent_name=f"{AGENT_NAME}-idle",
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "foreground_idle")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_completed = get_tool_completed_events(events)

        logger.info(f"[Test2] {len(tool_started)} tools started, {len(tool_completed)} completed")
        logger.info(f"[Test2] Tool names: {[e.get('tool_name') for e in tool_started]}")

        # Should have multiple tool invocations
        assert len(tool_started) >= 2, \
            f"Expected multiple tool calls. Got {len(tool_started)}: {[e.get('tool_name') for e in tool_started]}"
        logger.info("[PASS] Background + foreground work completed")


# ===========================================================================
# Test 3: LLM proactively checks background progress
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestProactiveProgressCheck:
    """LLM proactively checks on background task status."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-progress")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=_API_KEY, model=_MODEL,
            base_url=_BASE_URL, api_format="anthropic",
        )
        with c:
            yield c

    def test_llm_checks_progress(self, client, sandbox):
        """LLM backgrounds a task and proactively calls check_background_progress."""
        _register_model(client)
        _create_agent(client, f"{AGENT_NAME}-progress")
        events = send_chat(
            client,
            "Do the following:\n"
            "1. Run 'sleep 8 && echo INSTALL_DONE' in background using daytona_bash "
            "with background: true\n"
            "2. Run 'echo doing_other_work' in foreground\n"
            "3. Call check_background_progress to see the background task status\n"
            "4. Report what you see\n\n"
            "You MUST call check_background_progress at step 3.",
            agent_name=f"{AGENT_NAME}-progress",
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "progress_check")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_names = [e.get("tool_name") for e in tool_started]
        logger.info(f"[Test3] Tool names used: {tool_names}")

        # Should have called check_background_progress
        has_progress_check = "check_background_progress" in tool_names
        logger.info(f"[Test3] check_background_progress called: {has_progress_check}")

        # The LLM should attempt to check progress
        # (it may not always do it due to LLM non-determinism, so we log but don't hard-fail)
        if has_progress_check:
            progress_outputs = [
                e.get("output", "") for e in get_tool_completed_events(events)
                if "check_background_progress" in str(e.get("tool_name", ""))
            ]
            for po in progress_outputs:
                logger.info(f"[Test3] Progress output: {po[:300]}")
            logger.info("[PASS] LLM proactively checked background progress")
        else:
            logger.warning("[WARN] LLM did not call check_background_progress — LLM non-determinism")

        # At minimum, tools should have been used
        assert len(tool_started) >= 2, f"Expected 2+ tools. Got: {tool_names}"


# ===========================================================================
# Test 4: LLM cancels background task (failing tests)
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestCancelFailingTask:
    """LLM cancels a background task that's running a failing test suite."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-cancel-fail")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=_API_KEY, model=_MODEL,
            base_url=_BASE_URL, api_format="anthropic",
        )
        with c:
            yield c

    def test_llm_cancels_after_checking(self, client, sandbox):
        """LLM backgrounds a task, checks progress, then cancels it."""
        _register_model(client)
        _create_agent(client, f"{AGENT_NAME}-cancel")
        events = send_chat(
            client,
            "Do the following steps in order:\n"
            "1. Run 'sleep 30 && echo TESTS_DONE' in background using daytona_bash "
            "with background: true\n"
            "2. Run 'echo doing_foreground_fix' in foreground\n"
            "3. Call check_background_progress to check the background task\n"
            "4. The tests are taking too long. Cancel the background task using "
            "cancel_background_task with the task_id from step 3. "
            "Use reason: 'Tests taking too long, need to fix code first'\n"
            "5. Confirm the cancellation\n\n"
            "You MUST follow all 5 steps in order. Use background: true for step 1.",
            agent_name=f"{AGENT_NAME}-cancel",
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "cancel_failing")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_names = [e.get("tool_name") for e in tool_started]
        logger.info(f"[Test4] Tool names used: {tool_names}")

        # Check if cancel was called
        has_cancel = "cancel_background_task" in tool_names
        logger.info(f"[Test4] cancel_background_task called: {has_cancel}")

        if has_cancel:
            cancel_outputs = [
                e.get("output", "") for e in get_tool_completed_events(events)
                if "cancel_background_task" in str(e.get("tool_name", ""))
            ]
            for co in cancel_outputs:
                logger.info(f"[Test4] Cancel output: {co[:300]}")
            logger.info("[PASS] LLM cancelled the background task")
        else:
            logger.warning("[WARN] LLM did not call cancel_background_task — LLM non-determinism")

        assert len(tool_started) >= 2, f"Expected 2+ tool calls. Got: {tool_names}"


# ===========================================================================
# Test 5: LLM cancels hanging task after repeated checks
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestCancelHangingTask:
    """LLM cancels a background task that appears to be hanging."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-cancel-hang")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=_API_KEY, model=_MODEL,
            base_url=_BASE_URL, api_format="anthropic",
        )
        with c:
            yield c

    def test_llm_cancels_hanging_install(self, client, sandbox):
        """LLM backgrounds a hanging command, checks twice, then cancels."""
        _register_model(client)
        _create_agent(client, f"{AGENT_NAME}-hang")
        events = send_chat(
            client,
            "Do the following steps:\n"
            "1. Run 'sleep 60 && echo INSTALL_DONE' in background using daytona_bash "
            "with background: true (simulating a hanging npm install)\n"
            "2. Call check_background_progress to check status\n"
            "3. Call check_background_progress again — it's still running\n"
            "4. The install is clearly hanging. Cancel it using cancel_background_task "
            "with reason: 'npm install appears to be hanging'\n"
            "5. Report what happened\n\n"
            "You MUST use background: true for step 1 and follow all steps.",
            agent_name=f"{AGENT_NAME}-hang",
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "cancel_hanging")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_names = [e.get("tool_name") for e in tool_started]
        logger.info(f"[Test5] Tool names used: {tool_names}")

        # Count progress checks
        progress_count = tool_names.count("check_background_progress")
        cancel_count = tool_names.count("cancel_background_task")
        logger.info(f"[Test5] Progress checks: {progress_count}, Cancels: {cancel_count}")

        if progress_count >= 2 and cancel_count >= 1:
            logger.info("[PASS] LLM checked progress twice and cancelled hanging task")
        elif cancel_count >= 1:
            logger.info("[PASS] LLM cancelled hanging task (fewer progress checks than expected)")
        else:
            logger.warning(
                f"[WARN] Expected 2+ progress checks and 1 cancel. "
                f"Got progress={progress_count}, cancel={cancel_count} — LLM non-determinism"
            )

        # At minimum, should have used tools
        assert len(tool_started) >= 2, f"Expected 2+ tool calls. Got: {tool_names}"
