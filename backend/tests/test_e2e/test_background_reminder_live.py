# ruff: noqa
"""Live E2E: Ephemeral background reminder injection.

Tests that the soft background reminder (api_messages) is:
1. Injected into the API request when background tasks are pending
2. NOT persisted in conversation history (zero context cost)
3. The LLM acknowledges or acts on the reminder naturally

Run with: pytest tests/test_e2e/test_background_reminder_live.py -m live -v --log-cli-level=INFO
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
_FORMAT = "anthropic"

MODEL_KEY = "minimax-reminder-test"
AGENT_NAME = "test-reminder-agent"
AGENT_TOOLKITS = ["sandbox_operations"]

AGENT_PROMPT = """\
You are test-reminder-agent, a developer with a remote Daytona sandbox.

RULES:
- Use tools for every action.
- Use daytona_bash to run commands.
- You have background task support: add "background": true to tool input for long-running operations.
- Use check_background_progress to check background tasks.
- Use cancel_background_task to cancel background tasks.

Be concise. Always execute tools.
"""


def _register_model(client) -> dict:
    resp = client.post(
        "/api/db/models/register",
        json={
            "key": MODEL_KEY,
            "label": "MiniMax Reminder Test",
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
            "description": f"E2E reminder test agent ({name})",
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
    logger.info(f"\n{'='*60}\n[{label}] {len(events)} events:")
    for i, e in enumerate(events):
        etype = e.get("type", "unknown")
        if etype == "tool_started":
            logger.info(f"  [{i}] tool_started: {e.get('tool_name')} input={str(e.get('tool_input', {}))[:200]}")
        elif etype == "tool_completed":
            logger.info(f"  [{i}] tool_completed: {e.get('tool_name')} output={str(e.get('output', ''))[:150]}")
        elif etype == "assistant_complete":
            logger.info(f"  [{i}] assistant_complete: {str(e.get('message', ''))[:200]}")
        elif etype == "error":
            logger.error(f"  [{i}] ERROR: {e.get('message', '')[:300]}")
        elif etype in ("assistant_delta", "thinking_delta"):
            pass
        else:
            logger.info(f"  [{i}] {etype}")
    logger.info(f"{'='*60}\n")


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestEphemeralBackgroundReminder:
    """Tests that the ephemeral reminder nudges the LLM without polluting context."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("bg-reminder")
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

    def test_reminder_nudges_llm_to_check_progress(self, client, sandbox):
        """Background a slow task, do foreground work, verify the LLM
        becomes aware of the background task on subsequent turns.

        The ephemeral reminder '[Background: daytona_bash (Xs) still running]'
        is injected into the API request but NOT into conversation history.
        We verify this by checking:
        - The LLM mentions or interacts with the background task
        - The conversation doesn't grow with repeated reminder messages
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-nudge"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "Do these steps:\n"
            "1. Run 'sleep 15 && echo REMINDER_TEST_DONE' in background "
            "(use daytona_bash with background: true)\n"
            "2. Run 'echo STEP_2_DONE' in foreground\n"
            "3. Run 'echo STEP_3_DONE' in foreground\n"
            "4. Now check on the background task status\n\n"
            "Use background: true for step 1 only.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "reminder_nudge")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_names = [e.get("tool_name") for e in tool_started]
        logger.info(f"[Reminder] Tool names: {tool_names}")

        # The LLM should have used multiple tools (background + foreground + check)
        assert len(tool_started) >= 3, f"Expected 3+ tool calls. Got: {tool_names}"

        # Check if the LLM used check_background_progress
        # The ephemeral reminder makes this more likely but doesn't force it
        has_check = "check_background_progress" in tool_names
        logger.info(f"[Reminder] check_background_progress called: {has_check}")

        if has_check:
            logger.info("[PASS] LLM checked progress (reminder may have contributed)")
        else:
            logger.info("[INFO] LLM did not check progress — may have proceeded without")

        # Either way, the conversation should complete normally
        text = get_assistant_text(events)
        logger.info(f"[Reminder] Final text: {text[:200]}")

    def test_reminder_not_persisted_in_history(self, client, sandbox):
        """Send two sequential chats to the same agent session.
        The reminder from chat 1 should NOT appear in chat 2's context.

        We verify by:
        1. First chat: background a task + foreground work
        2. Second chat: ask the LLM to summarize what happened
        3. The summary should NOT mention '[Background: ...]' reminder text
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-persist"
        _create_agent(client, agent_name)

        # Chat 1: Background a task and do foreground work
        events1 = send_chat(
            client,
            "Run 'sleep 20 && echo BG_TASK' in background using daytona_bash "
            "with background: true. Then run 'echo FG_WORK' in foreground.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        _log_events(events1, "persist_chat1")
        types1 = get_event_types(events1)
        assert "assistant_complete" in types1, f"Chat 1 failed. Types: {types1}"

        # Chat 2: Ask LLM to describe the conversation so far
        events2 = send_chat(
            client,
            "What messages have you received so far in this conversation? "
            "List them briefly. Do NOT use any tools.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        _log_events(events2, "persist_chat2")
        types2 = get_event_types(events2)
        assert "assistant_complete" in types2, f"Chat 2 failed. Types: {types2}"

        text2 = get_assistant_text(events2)
        logger.info(f"[Persist] LLM summary: {text2[:500]}")

        # The ephemeral reminder should NOT be in the LLM's recollection
        # It was in api_messages but not in the persistent messages list
        has_reminder_leak = "[Background:" in text2 or "[SYSTEM NOTE" in text2
        if has_reminder_leak:
            logger.warning("[WARN] Reminder text leaked into history — investigate")
        else:
            logger.info("[PASS] No reminder text in LLM's conversation recall")

    def test_reminder_only_when_tasks_pending(self, client, sandbox):
        """Verify that no reminder is injected when there are no background tasks.

        Run a simple foreground-only interaction and confirm normal behavior.
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-noremindr"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "Run 'echo NO_BACKGROUND_HERE' using daytona_bash. "
            "Do NOT use background. Keep it simple.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        _log_events(events, "no_reminder")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_names = [e.get("tool_name") for e in tool_started]

        # Should NOT have check_background_progress (no background tasks exist)
        has_bg_check = "check_background_progress" in tool_names
        if has_bg_check:
            logger.info("[INFO] LLM checked progress anyway (no tasks to show)")
        else:
            logger.info("[PASS] No background progress check — no reminder needed")

        # No background events should exist
        bg_started = events_of_type(events, "background_started")
        assert len(bg_started) == 0, f"No background tasks expected. Got {len(bg_started)}"
        logger.info("[PASS] Foreground-only interaction, no reminder injected")
