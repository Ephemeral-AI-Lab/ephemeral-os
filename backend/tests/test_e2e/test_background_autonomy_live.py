# ruff: noqa
"""Live E2E: LLM autonomous background task decision-making.

Tests that the LLM independently decides when to check and cancel
background tasks — NO explicit instructions to check or cancel.
The ephemeral reminder is the only signal. The LLM must use its
own judgment.

Run with: pytest tests/test_e2e/test_background_autonomy_live.py -m live -v --log-cli-level=INFO
"""

from __future__ import annotations

import logging
import os

import pytest

from tests.test_e2e.conftest import (
    HAS_BOTH,
    MINIMAX_KEY,
    MINIMAX_MODEL,
    create_test_sandbox,
    delete_test_sandbox,
    events_of_type,
    get_assistant_text,
    get_event_types,
    get_tool_started_events,
    get_tool_completed_events,
    make_live_client,
    send_chat,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_API_KEY = os.environ.get("MINIMAX_API_KEY") or MINIMAX_KEY
_MODEL = os.environ.get("MINIMAX_MODEL") or MINIMAX_MODEL
_BASE_URL = "https://api.minimax.io/anthropic"

MODEL_KEY = "minimax-autonomy-test"
AGENT_NAME = "test-autonomy-agent"
AGENT_TOOLKITS = ["sandbox_operations"]

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


def _register_model(client) -> dict:
    resp = client.post(
        "/api/db/models/register",
        json={
            "key": MODEL_KEY,
            "label": "MiniMax Autonomy Test",
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
    resp = client.post(
        "/api/agents/",
        json={
            "name": name,
            "description": f"E2E autonomy test ({name})",
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
    tool_started = get_tool_started_events(events)
    tool_completed = get_tool_completed_events(events)
    assistant_turns = events_of_type(events, "assistant_complete")
    errors = events_of_type(events, "error")

    tool_names = [e.get("tool_name") for e in tool_started]

    logger.info(
        f"\n{'='*60}\n"
        f"[{label}]\n"
        f"  Tools: {len(tool_started)} started, {len(tool_completed)} completed\n"
        f"  Turns: {len(assistant_turns)}\n"
        f"  Errors: {len(errors)}\n"
        f"  Sequence: {tool_names}\n"
        f"{'='*60}"
    )

    # Log check/cancel decisions specifically
    checks = [n for n in tool_names if n == "check_background_progress"]
    cancels = [n for n in tool_names if n == "cancel_background_task"]
    logger.info(
        f"  LLM autonomous decisions: "
        f"{len(checks)} progress checks, {len(cancels)} cancels"
    )

    for e in errors:
        logger.error(f"  ERROR: {e.get('message', '')[:300]}")

    text = get_assistant_text(events)
    if text:
        logger.info(f"  Final text: {text[:300]}")


# ===========================================================================
# Test 1: LLM decides on its own to check background progress
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestAutonomousProgressCheck:
    """No instruction to check — LLM decides on its own."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("auto-check")
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

    def test_llm_autonomously_checks_progress(self, client, sandbox):
        """Give the LLM a background task and foreground work.
        Do NOT tell it to check progress. See if it does on its own.
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-autocheck"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "I need you to do two things:\n"
            "- Run a long build: 'sleep 20 && echo BUILD_OK' in background\n"
            "- While it runs, create a file /workspace/readme.txt with "
            "'Hello World' using daytona_bash: echo 'Hello World' > /workspace/readme.txt\n"
            "- Then read it back: cat /workspace/readme.txt\n\n"
            "Let me know when everything is done.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "autonomous_check")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_names = [e.get("tool_name") for e in get_tool_started_events(events)]
        has_check = "check_background_progress" in tool_names

        if has_check:
            logger.info("[RESULT] LLM AUTONOMOUSLY checked background progress")
        else:
            logger.info("[RESULT] LLM did NOT check progress on its own — completed without checking")

        # Either way the agent should complete. We log the decision, not assert it.
        assert len(tool_names) >= 2, f"Expected 2+ tools. Got: {tool_names}"
        logger.info(f"[DONE] Autonomous check test: checked={has_check}")


# ===========================================================================
# Test 2: LLM decides on its own to cancel a hanging task
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestAutonomousCancel:
    """Background a task that will never finish (sleep 120).
    The LLM must decide on its own to cancel or wait.
    The idle wait timeout will fire after 300s if the LLM doesn't act.
    """

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("auto-cancel")
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

    def test_llm_autonomously_handles_long_task(self, client, sandbox):
        """Background a very long task. Give foreground work. See what happens.
        The LLM might: check progress, cancel, or just wait for idle timeout.
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-autocancel"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "Run 'sleep 120 && echo NEVER_FINISHES' in background.\n"
            "Then run 'echo quick_task_done' in foreground.\n\n"
            "The background task simulates a very slow npm install. "
            "Use your judgment on what to do about it.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=360,
        )
        _log_events(events, "autonomous_cancel")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_names = [e.get("tool_name") for e in get_tool_started_events(events)]
        has_check = "check_background_progress" in tool_names
        has_cancel = "cancel_background_task" in tool_names

        logger.info(
            f"[RESULT] LLM autonomous decisions: "
            f"checked={has_check}, cancelled={has_cancel}"
        )

        if has_cancel:
            logger.info("[RESULT] LLM AUTONOMOUSLY cancelled the long task")
        elif has_check:
            logger.info("[RESULT] LLM checked progress but decided to wait")
        else:
            logger.info("[RESULT] LLM did not interact with background task — relied on idle wait")

        assert len(tool_names) >= 1, f"Expected 1+ tools. Got: {tool_names}"
        logger.info(f"[DONE] Autonomous cancel test: check={has_check}, cancel={has_cancel}")


# ===========================================================================
# Test 3: Multi-task autonomy — LLM manages two background tasks
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestAutonomousMultiTask:
    """Two background tasks. LLM must manage them independently."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("auto-multi")
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

    def test_llm_manages_multiple_background_tasks(self, client, sandbox):
        """Two background tasks with different durations. See how LLM manages."""
        _register_model(client)
        agent_name = f"{AGENT_NAME}-automulti"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "I need two things running in the background:\n"
            "- A fast build: 'sleep 10 && echo FAST_BUILD_DONE' in background\n"
            "- A slow test suite: 'sleep 60 && echo SLOW_TESTS_DONE' in background\n\n"
            "While those run, create /workspace/status.txt with 'waiting for builds' "
            "using daytona_bash.\n\n"
            "Manage the background tasks as you see fit.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=360,
        )
        _log_events(events, "autonomous_multi")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_names = [e.get("tool_name") for e in get_tool_started_events(events)]
        checks = tool_names.count("check_background_progress")
        cancels = tool_names.count("cancel_background_task")
        bash_calls = tool_names.count("daytona_bash")

        logger.info(
            f"[RESULT] Multi-task autonomy:\n"
            f"  bash calls: {bash_calls}\n"
            f"  progress checks: {checks}\n"
            f"  cancels: {cancels}\n"
            f"  total tools: {len(tool_names)}"
        )

        # The LLM should at minimum run the foreground task
        assert bash_calls >= 1, f"Expected 1+ bash calls. Got: {tool_names}"
        logger.info(f"[DONE] Multi-task autonomy: {len(tool_names)} total tools")
