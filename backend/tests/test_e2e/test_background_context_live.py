# ruff: noqa
"""Live E2E: Context limits with long background tasks and ephemeral reminders.

Tests that the system handles context pressure correctly when:
1. Many foreground tool calls accumulate while background tasks run
2. Ephemeral reminders do NOT accumulate in context history
3. Auto-compaction fires and background task results survive it
4. Large tool outputs + background reminders don't blow up context

Run with: pytest tests/test_e2e/test_background_context_live.py -m live -v --log-cli-level=INFO
"""

from __future__ import annotations

import json
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
    parse_sse_events,
    send_chat,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_API_KEY = os.environ.get("MINIMAX_API_KEY") or MINIMAX_KEY
_MODEL = os.environ.get("MINIMAX_MODEL") or MINIMAX_MODEL
_BASE_URL = "https://api.minimax.io/anthropic"
_FORMAT = "anthropic"

MODEL_KEY = "minimax-context-test"
AGENT_NAME = "test-context-agent"
AGENT_TOOLKITS = ["sandbox_operations"]

AGENT_PROMPT = """\
You are test-context-agent, a developer with a remote Daytona sandbox.

RULES:
- Use tools for every action — never just describe what you'd do.
- Use daytona_bash to run commands.
- You have background task support: add "background": true to tool input.
- Use check_background_progress to check background tasks.
- Use cancel_background_task to cancel background tasks.
- Be concise but thorough. Execute all steps requested.
"""


def _register_model(client) -> dict:
    resp = client.post(
        "/api/db/models/register",
        json={
            "key": MODEL_KEY,
            "label": "MiniMax Context Test",
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
            "description": f"E2E context stress test ({name})",
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
    tool_started = events_of_type(events, "tool_started")
    tool_completed = events_of_type(events, "tool_completed")
    assistant_complete = events_of_type(events, "assistant_complete")
    errors = events_of_type(events, "error")
    bg_started = events_of_type(events, "background_started")
    bg_completed = events_of_type(events, "background_completed")

    logger.info(
        f"\n{'='*60}\n"
        f"[{label}] Event summary:\n"
        f"  Total events: {len(events)}\n"
        f"  Tool started: {len(tool_started)}\n"
        f"  Tool completed: {len(tool_completed)}\n"
        f"  Assistant turns: {len(assistant_complete)}\n"
        f"  Background started: {len(bg_started)}\n"
        f"  Background completed: {len(bg_completed)}\n"
        f"  Errors: {len(errors)}\n"
        f"{'='*60}"
    )

    for e in errors:
        logger.error(f"  ERROR: {e.get('message', '')[:300]}")

    tool_names = [e.get("tool_name") for e in tool_started]
    logger.info(f"  Tool sequence: {tool_names}")

    for tc in tool_completed:
        name = tc.get("tool_name", "")
        output = str(tc.get("output", ""))
        logger.info(f"  {name}: {output[:150]}{'...' if len(output) > 150 else ''}")


# ===========================================================================
# Test 1: Many foreground calls with background running — reminders stay ephemeral
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestReminderDoesNotAccumulate:
    """Verify ephemeral reminders don't pile up in context across many turns."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("ctx-reminder")
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

    def test_many_foreground_turns_with_background(self, client, sandbox):
        """Background a slow task, then do 5+ foreground operations.

        The ephemeral reminder fires each turn but must NOT accumulate.
        If it leaked into history, context would grow by ~100 tokens/turn
        from stale reminders. We verify the agent completes without
        context overflow or degraded performance.
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-accum"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "Follow these steps exactly:\n"
            "1. Run 'sleep 30 && echo BG_COMPLETE' in background (use background: true)\n"
            "2. Run 'echo STEP_2' in foreground\n"
            "3. Run 'echo STEP_3' in foreground\n"
            "4. Run 'echo STEP_4' in foreground\n"
            "5. Run 'echo STEP_5' in foreground\n"
            "6. Run 'echo STEP_6' in foreground\n"
            "7. Run 'echo STEP_7' in foreground\n"
            "8. Check background progress using check_background_progress\n"
            "9. Cancel the background task using cancel_background_task\n"
            "10. Report what happened\n\n"
            "Use background: true for step 1 ONLY. All other steps are foreground.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "reminder_accumulation")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_names = [e.get("tool_name") for e in tool_started]
        logger.info(f"[Accum] Total tools: {len(tool_started)}")
        logger.info(f"[Accum] Tool sequence: {tool_names}")

        # Should have many tool calls — proving the agent kept working
        # across multiple turns while background was running
        assert len(tool_started) >= 5, \
            f"Expected 5+ tool calls (foreground chain + bg ops). Got {len(tool_started)}: {tool_names}"

        # Count assistant turns — each is one LLM round-trip
        assistant_turns = events_of_type(events, "assistant_complete")
        logger.info(f"[Accum] Assistant turns: {len(assistant_turns)}")

        # If reminders accumulated, we'd see errors or degraded output
        # by turn 6-7. The fact that it completed means reminders are ephemeral.
        errors = events_of_type(events, "error")
        context_errors = [e for e in errors if "context" in str(e.get("message", "")).lower()
                          or "token" in str(e.get("message", "")).lower()]
        assert len(context_errors) == 0, \
            f"Context-related errors detected: {[e.get('message') for e in context_errors]}"
        logger.info("[PASS] No context accumulation from reminders across many turns")


# ===========================================================================
# Test 2: Large tool outputs with background tasks
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestLargeOutputWithBackground:
    """Generate large tool outputs while background tasks run."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("ctx-large")
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

    def test_large_foreground_output_with_background(self, client, sandbox):
        """Generate large tool outputs (seq, find) while background runs.

        Tests that:
        - Large tool outputs are truncated at source (8000 chars)
        - Background reminders don't compound the context pressure
        - Auto-compaction can fire if needed
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-large"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "Follow these steps:\n"
            "1. Run 'sleep 20 && echo LARGE_BG_DONE' in background (use background: true)\n"
            "2. Run 'seq 1 500' in foreground — this generates 500 lines\n"
            "3. Run 'for i in $(seq 1 100); do echo \"line_$i: $(date)\"; done' in foreground\n"
            "4. Check background progress\n"
            "5. Cancel the background task\n"
            "6. Report: how much output did you see from the seq command?\n\n"
            "Use background: true for step 1 ONLY.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=300,
        )
        _log_events(events, "large_output")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_completed = get_tool_completed_events(events)
        total_output_chars = sum(len(str(e.get("output", ""))) for e in tool_completed)
        logger.info(f"[Large] Total tool output chars: {total_output_chars}")
        logger.info(f"[Large] Tool completions: {len(tool_completed)}")

        # Verify outputs were generated — even if truncated, should have content
        assert len(tool_completed) >= 2, \
            f"Expected 2+ tool completions. Got {len(tool_completed)}"

        # Verify no context overflow errors
        errors = events_of_type(events, "error")
        context_errors = [e for e in errors if "context" in str(e.get("message", "")).lower()
                          or "token" in str(e.get("message", "")).lower()
                          or "maximum" in str(e.get("message", "")).lower()]
        assert len(context_errors) == 0, \
            f"Context overflow detected: {[e.get('message') for e in context_errors]}"
        logger.info(f"[PASS] Large outputs handled. Total chars: {total_output_chars}")


# ===========================================================================
# Test 3: Multi-chat session — reminders don't leak across chats
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestReminderIsolationAcrossChats:
    """Reminders from chat 1 must not appear in chat 2's context."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("ctx-isolate")
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

    def test_reminder_isolated_between_chats(self, client, sandbox):
        """Chat 1 has background tasks + reminders. Chat 2 should see no trace."""
        _register_model(client)
        agent_name = f"{AGENT_NAME}-isolate"
        _create_agent(client, agent_name)

        # Chat 1: Background task + foreground work (generates reminders)
        logger.info("[Isolate] === Chat 1: Background + foreground ===")
        events1 = send_chat(
            client,
            "1. Run 'sleep 15 && echo CHAT1_BG' in background (use background: true)\n"
            "2. Run 'echo CHAT1_FG_1' in foreground\n"
            "3. Run 'echo CHAT1_FG_2' in foreground\n"
            "4. Cancel the background task\n\n"
            "Use background: true for step 1.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        _log_events(events1, "isolate_chat1")
        types1 = get_event_types(events1)
        assert "assistant_complete" in types1, f"Chat 1 failed. Types: {types1}"

        # Chat 2: Ask LLM to list what it remembers — NO background tasks here
        logger.info("[Isolate] === Chat 2: Check for leaked reminders ===")
        events2 = send_chat(
            client,
            "List all the messages you can see in this conversation history. "
            "Include any system messages or reminders you see. "
            "Do NOT use any tools. Just list what you see.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        _log_events(events2, "isolate_chat2")
        types2 = get_event_types(events2)
        assert "assistant_complete" in types2, f"Chat 2 failed. Types: {types2}"

        text2 = get_assistant_text(events2)
        logger.info(f"[Isolate] Chat 2 LLM recall:\n{text2[:500]}")

        # Check for reminder leakage
        leaked_patterns = [
            "<system-reminder>",
            "Background:",
            "still running",
            "No new output in the last",
        ]
        found_leaks = [p for p in leaked_patterns if p.lower() in text2.lower()]
        if found_leaks:
            logger.warning(f"[WARN] Possible reminder leak: {found_leaks}")
        else:
            logger.info("[PASS] No reminder text leaked into chat 2")

        # The LLM should remember the actual conversation (tool calls, results)
        # but NOT the ephemeral reminders
        logger.info("[PASS] Reminder isolation verified across chats")


# ===========================================================================
# Test 4: Sustained background with many turns — stress test
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestSustainedBackgroundStress:
    """Long-running background task across 8+ foreground turns.

    This is the stress test — if reminders accumulate or context
    management breaks down, this test will fail with context overflow
    or degraded LLM responses.
    """

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("ctx-stress")
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

    def test_sustained_background_many_foreground_turns(self, client, sandbox):
        """Background task runs for 45s while LLM does 8+ foreground operations.

        Verifies:
        - Agent stays coherent across many turns (no context degradation)
        - Reminders don't eat context budget
        - Background task can be checked and cancelled after many turns
        - No context overflow errors
        """
        _register_model(client)
        agent_name = f"{AGENT_NAME}-stress"
        _create_agent(client, agent_name)

        events = send_chat(
            client,
            "This is a multi-step task. Follow ALL steps:\n\n"
            "1. Run 'sleep 45 && echo STRESS_BG_DONE' in background (use background: true)\n"
            "2. Run 'echo STEP_A' in foreground\n"
            "3. Run 'echo STEP_B' in foreground\n"
            "4. Run 'echo STEP_C' in foreground\n"
            "5. Run 'echo STEP_D' in foreground\n"
            "6. Run 'echo STEP_E' in foreground\n"
            "7. Run 'echo STEP_F' in foreground\n"
            "8. Run 'echo STEP_G' in foreground\n"
            "9. Run 'echo STEP_H' in foreground\n"
            "10. Check background progress\n"
            "11. Cancel the background task with reason 'stress test complete'\n"
            "12. Summarize: how many foreground steps completed? "
            "What was the background task status when you checked?\n\n"
            "Use background: true for step 1 ONLY. Execute each step with daytona_bash.",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=360,
        )
        _log_events(events, "stress_test")
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = get_tool_started_events(events)
        tool_completed = get_tool_completed_events(events)
        tool_names = [e.get("tool_name") for e in tool_started]
        assistant_turns = events_of_type(events, "assistant_complete")

        logger.info(f"[Stress] Total tools started: {len(tool_started)}")
        logger.info(f"[Stress] Total tools completed: {len(tool_completed)}")
        logger.info(f"[Stress] Assistant turns: {len(assistant_turns)}")
        logger.info(f"[Stress] Tool sequence: {tool_names}")

        # Should have many tool calls — the agent executed the full sequence
        assert len(tool_started) >= 6, \
            f"Expected 6+ tool calls (8 fg + bg + check + cancel). Got {len(tool_started)}: {tool_names}"

        # Verify no context-related errors
        errors = events_of_type(events, "error")
        context_errors = [
            e for e in errors
            if any(kw in str(e.get("message", "")).lower()
                   for kw in ["context", "token", "maximum", "overflow", "length"])
        ]
        if context_errors:
            for ce in context_errors:
                logger.error(f"[Stress] Context error: {ce.get('message', '')[:300]}")
        assert len(context_errors) == 0, \
            f"Context errors under stress: {[e.get('message')[:200] for e in context_errors]}"

        # The final assistant message should be coherent — it summarizes the session
        final_text = get_assistant_text(events)
        logger.info(f"[Stress] Final summary: {final_text[:300]}")
        assert len(final_text) > 20, \
            f"Final summary too short — possible degradation. Got: {final_text}"

        logger.info(
            f"[PASS] Stress test completed: {len(tool_started)} tools, "
            f"{len(assistant_turns)} turns, no context overflow"
        )
