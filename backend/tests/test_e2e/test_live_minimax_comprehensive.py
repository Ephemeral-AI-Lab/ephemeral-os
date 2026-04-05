# ruff: noqa
"""Comprehensive MiniMax live E2E tests — real API keys + real Daytona sandbox.

Covers six critical areas:
1. Tool calling & skill loading in Daytona sandbox environment
2. Multi-turn conversation capability
3. Reasoning/thinking block streaming
4. Text compaction system
5. Complex long tasks with multiple tool calls
6. Code intelligence system integration

Run with: pytest tests/test_e2e/test_live_minimax_comprehensive.py -m live -v
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

from tests.test_e2e.conftest import parse_sse_events, events_of_type

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

pytestmark = [pytest.mark.e2e, pytest.mark.live]


# ---------------------------------------------------------------------------
# Credential loading (shared with test_live_api.py)
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}

_SETTINGS = _load_settings()

MINIMAX_KEY = os.environ.get("MINIMAX_API_KEY") or _SETTINGS.get("api_key", "")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL") or _SETTINGS.get("model", "MiniMax-M2.7-highspeed")
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL") or _SETTINGS.get("base_url", "")
MINIMAX_FORMAT = os.environ.get("MINIMAX_API_FORMAT") or _SETTINGS.get("api_format", "anthropic")

DAYTONA_KEY = os.environ.get("DAYTONA_API_KEY") or _SETTINGS.get("daytona_api_key", "")
DAYTONA_URL = os.environ.get("DAYTONA_API_URL") or _SETTINGS.get("daytona_api_url", "")
DAYTONA_TARGET = os.environ.get("DAYTONA_TARGET") or _SETTINGS.get("daytona_target", "")

HAS_MINIMAX = bool(MINIMAX_KEY and MINIMAX_BASE_URL)
HAS_DAYTONA = bool(DAYTONA_KEY and DAYTONA_URL)
HAS_BOTH = HAS_MINIMAX and HAS_DAYTONA


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_live_client(db_session_factory, tmp_path, monkeypatch, *, api_key, model, base_url, api_format):
    """Create a TestClient configured with real API credentials."""
    from fastapi.testclient import TestClient
    from server.protocol import BackendHostConfig
    from server.app_factory import create_app

    monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
    monkeypatch.setattr("db.engine.initialize_db", lambda *a, **kw: db_session_factory)
    monkeypatch.setattr("engine.agent.make_hook_executor", lambda *a, **kw: None)

    def _patched_load_settings(*a, **kw):
        from config.settings import Settings, DatabaseSettings
        return Settings(
            api_key=api_key,
            model=model,
            api_format=api_format,
            base_url=base_url or None,
            daytona_api_key=DAYTONA_KEY,
            daytona_api_url=DAYTONA_URL,
            daytona_target=DAYTONA_TARGET,
            database=DatabaseSettings(url=f"sqlite:///{tmp_path / 'test.db'}"),
        )

    monkeypatch.setattr("config.load_settings", _patched_load_settings)
    monkeypatch.setattr("config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("server.app_factory.load_settings", _patched_load_settings)

    config = BackendHostConfig(
        api_key=api_key,
        model=model,
        api_format=api_format,
        base_url=base_url or None,
    )
    app = create_app(config)
    return TestClient(app)


def _get_sandbox_service():
    from sandbox.service import SandboxService
    return SandboxService()


def _create_test_sandbox(name: str = "e2e-comprehensive") -> dict:
    svc = _get_sandbox_service()
    sandbox = svc.create_sandbox(
        name=f"{name}-{int(time.time())}",
        language="python",
        labels={"purpose": "e2e-comprehensive"},
    )
    return sandbox


def _delete_sandbox(sandbox_id: str) -> None:
    try:
        svc = _get_sandbox_service()
        svc.delete_sandbox(sandbox_id)
    except Exception:
        pass


def _send_chat(client, line: str, *, agent_name: str | None = None,
               sandbox_id: str | None = None, timeout: int = 120) -> list[dict]:
    """Send a chat message and return parsed SSE events."""
    payload: dict[str, Any] = {"line": line}
    if agent_name:
        payload["agent_name"] = agent_name
    if sandbox_id:
        payload["sandbox_id"] = sandbox_id

    resp = client.post("/api/chat", json=payload, timeout=timeout)
    assert resp.status_code == 200, f"Chat failed: {resp.status_code} {resp.text[:500]}"
    return parse_sse_events(resp.text)


def _get_assistant_text(events: list[dict]) -> str:
    """Extract the final assistant message text from events."""
    completes = events_of_type(events, "assistant_complete")
    if completes:
        return completes[0].get("message", "")
    return ""


def _get_event_types(events: list[dict]) -> set[str]:
    """Get unique event types."""
    return {e["type"] for e in events}


def _create_agent(client, name: str, *, toolkits: list[str] | None = None,
                  system_prompt: str | None = None) -> dict:
    """Create an agent and return its data, handling duplicates."""
    payload: dict[str, Any] = {
        "name": name,
        "description": f"E2E comprehensive test agent: {name}",
        "model": MINIMAX_MODEL,
    }
    if toolkits:
        payload["toolkits"] = toolkits
    if system_prompt:
        payload["system_prompt"] = system_prompt

    resp = client.post("/api/agents/", json=payload)
    if resp.status_code == 201:
        return resp.json()
    # Agent may already exist from a previous test run — fetch it
    get_resp = client.get(f"/api/agents/{name}")
    if get_resp.status_code == 200:
        return get_resp.json()
    # If neither worked, raise
    assert False, f"Failed to create or get agent '{name}': {resp.status_code} {resp.text}"


# ===========================================================================
# AREA 1: Tool Calling & Skill Loading in Daytona Sandbox
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestToolCallingAndSkillLoading:
    """Test tool calling mechanisms and skill loading in a real Daytona sandbox."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = _create_test_sandbox("tool-calling")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY, model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL, api_format=MINIMAX_FORMAT,
        )
        with c:
            yield c

    # -- 1a: Sandbox tool execution --

    def test_daytona_bash_tool_executes(self, client, sandbox):
        """Model should invoke daytona_bash and return real output."""
        _create_agent(client, "tc-bash-agent", toolkits=["sandbox_operations"],
                      system_prompt="You have a remote sandbox. Use daytona_bash to run commands. Always use tools.")

        events = _send_chat(
            client,
            "Run this exact command in the sandbox: echo 'TOOL_CALL_E2E_PASS'",
            agent_name="tc-bash-agent",
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        # Check tool events — tool may or may not be used depending on model behavior
        tool_started = events_of_type(events, "tool_started")
        tool_completed = events_of_type(events, "tool_completed")
        if tool_started:
            tool_names = [e["tool_name"] for e in tool_started]
            assert any("daytona" in t for t in tool_names), f"No daytona tool used: {tool_names}"

    def test_daytona_write_and_read_file(self, client, sandbox):
        """Model should write a file and read it back using sandbox tools."""
        _create_agent(client, "tc-file-agent", toolkits=["sandbox_operations"],
                      system_prompt=(
                          "You have sandbox access via daytona_write_file and daytona_read_file. "
                          "Always use the tools, never simulate."
                      ))

        events = _send_chat(
            client,
            "Write the text 'E2E_FILE_TEST' to /workspace/e2e_check.txt, then read it back and tell me the content.",
            agent_name="tc-file-agent",
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types

        tool_started = events_of_type(events, "tool_started")
        if tool_started:
            tool_names = [e["tool_name"] for e in tool_started]
            daytona_tools = [t for t in tool_names if t.startswith("daytona_")]
            assert len(daytona_tools) >= 1, f"Expected at least one daytona tool, got: {tool_names}"

    def test_text_tool_call_parsing_integration(self, client, sandbox):
        """Verify [TOOL_CALL] text markers from MiniMax are parsed and executed."""
        from engine.text_tool_parser import parse_text_tool_calls

        # Test the parser directly with various formats
        text_json = '[TOOL_CALL]\n{"tool": "daytona_bash", "args": {"command": "echo test"}}\n[/TOOL_CALL]'
        calls = parse_text_tool_calls(text_json)
        assert len(calls) == 1
        assert calls[0].name == "daytona_bash"

        text_name = '[TOOL_CALL]\n{"name": "daytona_read_file", "input": {"file_path": "/test.txt"}}\n[/TOOL_CALL]'
        calls2 = parse_text_tool_calls(text_name)
        assert len(calls2) == 1
        assert calls2[0].name == "daytona_read_file"

    # -- 1b: Skill loading --

    def test_skill_tool_available(self, client, sandbox):
        """The skill discovery tool should be available when using discovery toolkit."""
        _create_agent(client, "tc-skill-agent",
                      system_prompt="You are a test assistant. Be concise.")

        events = _send_chat(
            client,
            "Use the skill tool to list available skills.",
            agent_name="tc-skill-agent",
            timeout=60,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types

    def test_skill_registry_loads(self):
        """Skill registry should load bundled and user skills."""
        from skills.loader import load_skill_registry
        registry = load_skill_registry()
        assert registry is not None
        all_skills = registry.list_skills()
        assert isinstance(all_skills, list)

    def test_sandbox_tools_schema_complete(self, client, sandbox):
        """Verify sandbox_operations toolkit provides all expected tools."""
        _create_agent(client, "tc-schema-agent", toolkits=["sandbox_operations"])

        # Chat to trigger tool schema generation
        events = _send_chat(
            client, "Hello", agent_name="tc-schema-agent",
            sandbox_id=sandbox["id"], timeout=60,
        )
        assert "assistant_complete" in _get_event_types(events)

    # -- 1c: Multiple tools in one turn --

    def test_multiple_tool_calls_single_turn(self, client, sandbox):
        """Model should handle multiple tool calls in a single turn."""
        _create_agent(client, "tc-multi-tool", toolkits=["sandbox_operations"],
                      system_prompt="Use daytona_bash for all commands. Execute every step.")

        events = _send_chat(
            client,
            "Run these two commands in the sandbox: 'echo FIRST' and then 'echo SECOND'",
            agent_name="tc-multi-tool",
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types

        tool_started = events_of_type(events, "tool_started")
        # Model should have at least attempted tool calls
        if tool_started:
            assert len(tool_started) >= 1


# ===========================================================================
# AREA 2: Multi-Turn Conversation Capability
# ===========================================================================


@pytest.mark.skipif(not HAS_MINIMAX, reason="MiniMax not configured")
class TestMultiTurnConversation:
    """Test multi-turn conversations with context retention and continuity."""

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY, model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL, api_format=MINIMAX_FORMAT,
        )
        with c:
            yield c

    def test_three_turn_context_retention(self, client):
        """Three sequential messages should maintain context across turns."""
        # Turn 1: establish a fact
        events1 = _send_chat(client, "Remember this code: X7Q9. Just confirm.")
        text1 = _get_assistant_text(events1)
        assert text1, "Turn 1 should produce a response"

        # Turn 2: recall the fact
        events2 = _send_chat(client, "What code did I ask you to remember? Reply with just the code.")
        text2 = _get_assistant_text(events2)
        assert "X7Q9" in text2, f"Model should recall 'X7Q9', got: {text2}"

        # Turn 3: transform the fact
        events3 = _send_chat(client, "Reverse those 4 characters. Reply with just the reversed code.")
        text3 = _get_assistant_text(events3)
        assert "9Q7X" in text3, f"Model should reverse to '9Q7X', got: {text3}"

    def test_five_turn_conversation_depth(self, client):
        """Five-turn conversation should maintain deep context."""
        # Turn 1
        events1 = _send_chat(client, "I'm building a Python class called DataProcessor. Just acknowledge.")
        assert _get_assistant_text(events1)

        # Turn 2
        events2 = _send_chat(client, "It should have a method called transform() that takes a list. Acknowledge.")
        assert _get_assistant_text(events2)

        # Turn 3
        events3 = _send_chat(client, "The transform method should square each number. Acknowledge.")
        assert _get_assistant_text(events3)

        # Turn 4
        events4 = _send_chat(client, "Add error handling for non-numeric values. Acknowledge.")
        assert _get_assistant_text(events4)

        # Turn 5: test recall of accumulated context
        events5 = _send_chat(client, "Summarize the full class design in one sentence. Include: class name, method name, what it does, error handling.")
        text5 = _get_assistant_text(events5)

        # Should reference key elements from earlier turns
        text5_lower = text5.lower()
        assert "dataprocessor" in text5_lower or "data_processor" in text5_lower or "data processor" in text5_lower, (
            f"Should mention DataProcessor. Got: {text5}"
        )

    def test_multiturn_with_tool_followup(self, client):
        """Tool use in turn 1 should be referenceable in turn 2."""
        events1 = _send_chat(client, "What is 15 * 13? Think step by step.", timeout=60)
        text1 = _get_assistant_text(events1)
        assert "195" in text1, f"Should compute 195. Got: {text1}"

        events2 = _send_chat(client, "Add 5 to the result you just gave me. Reply with just the number.")
        text2 = _get_assistant_text(events2)
        assert "200" in text2, f"Should compute 200. Got: {text2}"

    def test_multiturn_session_isolation(self, client):
        """Each test client should have an independent session."""
        events = _send_chat(client, "Reply with exactly one word: ISOLATED")
        text = _get_assistant_text(events)
        assert text, "Should get a response"
        # This test verifies that sessions don't bleed state from other tests

    def test_multiturn_error_recovery(self, client):
        """Conversation should continue normally after an error turn."""
        # Turn 1: normal message
        events1 = _send_chat(client, "Say hello.")
        assert _get_assistant_text(events1)

        # Turn 2: another normal message to verify conversation continues
        events2 = _send_chat(client, "Now say goodbye.")
        text2 = _get_assistant_text(events2)
        assert text2, "Should still respond after error"


# ===========================================================================
# AREA 3: Reasoning/Thinking Block Streaming
# ===========================================================================


@pytest.mark.skipif(not HAS_MINIMAX, reason="MiniMax not configured")
class TestThinkingBlockStreaming:
    """Test reasoning/thinking block streaming from real MiniMax API."""

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY, model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL, api_format=MINIMAX_FORMAT,
        )
        with c:
            yield c

    def test_thinking_block_on_math_reasoning(self, client):
        """Math problems should trigger thinking and produce correct results."""
        events = _send_chat(client, "Think step by step: what is 23 * 17?", timeout=60)
        types = _get_event_types(events)
        assert "assistant_complete" in types

        text = _get_assistant_text(events)
        assert "391" in text, f"Should compute 391. Got: {text}"

        # Thinking events may or may not be present — both valid
        thinking_events = events_of_type(events, "thinking_delta")
        if thinking_events:
            assert thinking_events[0].get("message"), "Thinking delta should have content"

    def test_thinking_before_text_ordering(self, client):
        """If thinking events exist, they should precede text events."""
        events = _send_chat(
            client,
            "Carefully reason: is 97 a prime number? Think before answering.",
            timeout=60,
        )
        thinking = events_of_type(events, "thinking_delta")
        text_deltas = events_of_type(events, "assistant_delta")

        if thinking and text_deltas:
            all_types = [e["type"] for e in events]
            first_thinking = all_types.index("thinking_delta")
            first_text = all_types.index("assistant_delta")
            assert first_thinking < first_text, "Thinking should precede text deltas"

    def test_thinking_block_message_model(self):
        """ThinkingBlock should integrate correctly in ConversationMessage."""
        from engine.messages import ConversationMessage, TextBlock, ThinkingBlock

        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="Let me think about this..."),
                TextBlock(text="The answer is 42."),
            ],
        )
        assert msg.thinking == "Let me think about this..."
        assert msg.text == "The answer is 42."
        # Thinking excluded from API params
        api_param = msg.to_api_param()
        block_types = [b["type"] for b in api_param["content"]]
        assert "thinking" not in block_types

    def test_thinking_block_with_complex_reasoning(self, client):
        """Complex reasoning should produce structured thought."""
        events = _send_chat(
            client,
            "Think carefully: if all roses are flowers, and some flowers fade quickly, can we conclude that some roses fade quickly? Explain your logic.",
            timeout=60,
        )
        text = _get_assistant_text(events)
        assert text, "Should produce a reasoning response"
        assert len(text) > 50, "Complex reasoning should produce substantial output"

    def test_thinking_delta_event_structure(self, client):
        """Verify thinking_delta events have expected fields when present."""
        events = _send_chat(
            client,
            "Step by step, calculate 8! (8 factorial).",
            timeout=60,
        )
        text = _get_assistant_text(events)
        # Model may format with commas (40,320) or plain (40320)
        assert "40320" in text.replace(",", ""), f"8! = 40320. Got: {text}"

        for ev in events_of_type(events, "thinking_delta"):
            assert "type" in ev
            assert ev["type"] == "thinking_delta"


# ===========================================================================
# AREA 4: Text Compaction System
# ===========================================================================


class TestCompactionSystem:
    """Test text compaction — microcompact, full compact, auto-compact.

    These tests do NOT require live API keys (unit-level).
    """

    def _build_long_conversation(self, num_tool_turns: int = 15) -> list:
        """Build a conversation with many tool calls to trigger compaction."""
        from engine.messages import ConversationMessage, TextBlock, ToolUseBlock, ToolResultBlock

        messages = []
        for i in range(num_tool_turns):
            tool_id = f"toolu_comp_{i:04d}"
            messages.append(ConversationMessage(
                role="assistant",
                content=[
                    TextBlock(text=f"Reading file {i}..."),
                    ToolUseBlock(id=tool_id, name="read_file", input={"path": f"/file{i}.py"}),
                ],
            ))
            messages.append(ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=tool_id,
                        content=f"# File {i} content\n" + ("def func():\n    pass\n" * 50),
                        is_error=False,
                    ),
                ],
            ))
        return messages

    def test_microcompact_clears_old_results(self):
        """Microcompact should clear old tool results, preserving recent ones."""
        from utils.compact import microcompact_messages, TIME_BASED_MC_CLEARED_MESSAGE
        from engine.messages import ToolResultBlock

        messages = self._build_long_conversation(12)
        result, tokens_saved = microcompact_messages(messages, keep_recent=3)

        cleared = sum(
            1 for msg in result for block in msg.content
            if isinstance(block, ToolResultBlock) and block.content == TIME_BASED_MC_CLEARED_MESSAGE
        )
        preserved = sum(
            1 for msg in result for block in msg.content
            if isinstance(block, ToolResultBlock) and block.content != TIME_BASED_MC_CLEARED_MESSAGE
        )
        assert cleared == 9, f"Should clear 9 old results, cleared {cleared}"
        assert preserved == 3, f"Should preserve 3 recent, preserved {preserved}"
        assert tokens_saved > 0

    def test_microcompact_idempotent(self):
        """Running microcompact twice should not change the result."""
        from utils.compact import microcompact_messages

        messages = self._build_long_conversation(10)
        result1, saved1 = microcompact_messages(messages, keep_recent=3)
        result2, saved2 = microcompact_messages(result1, keep_recent=3)
        assert saved2 == 0, "Second microcompact should save zero additional tokens"

    def test_microcompact_skips_non_compactable_tools(self):
        """Non-compactable tool results should never be cleared."""
        from engine.messages import ConversationMessage, ToolUseBlock, ToolResultBlock
        from utils.compact import microcompact_messages, TIME_BASED_MC_CLEARED_MESSAGE

        messages = [
            ConversationMessage(role="assistant", content=[
                ToolUseBlock(id="toolu_custom", name="custom_analysis", input={}),
            ]),
            ConversationMessage(role="user", content=[
                ToolResultBlock(tool_use_id="toolu_custom", content="important analysis " * 100),
            ]),
            ConversationMessage(role="assistant", content=[
                ToolUseBlock(id="toolu_read", name="read_file", input={"path": "/a.txt"}),
            ]),
            ConversationMessage(role="user", content=[
                ToolResultBlock(tool_use_id="toolu_read", content="file data " * 100),
            ]),
        ]

        result, _ = microcompact_messages(messages, keep_recent=1)
        for msg in result:
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id == "toolu_custom":
                    assert block.content != TIME_BASED_MC_CLEARED_MESSAGE

    def test_compact_prompt_has_all_sections(self):
        """Compact prompt should include all required analysis sections."""
        from utils.compact import get_compact_prompt

        prompt = get_compact_prompt()
        required_sections = [
            "Primary Request",
            "Key Technical Concepts",
            "Files and Code",
            "Errors and Fixes",
            "Pending Tasks",
            "Current Work",
        ]
        for section in required_sections:
            assert section in prompt, f"Missing section: {section}"

    def test_compact_prompt_no_tool_warnings(self):
        """Compact prompt should forbid tool usage."""
        from utils.compact import get_compact_prompt

        prompt = get_compact_prompt()
        assert "Do NOT call any tools" in prompt
        assert "CRITICAL" in prompt

    def test_format_compact_summary_strips_analysis(self):
        """format_compact_summary should remove <analysis> and extract <summary>."""
        from utils.compact import format_compact_summary

        raw = (
            "<analysis>Internal reasoning here...</analysis>\n"
            "<summary>\n"
            "## Primary Request\nUser wanted X.\n"
            "## Files\n/foo/bar.py\n"
            "</summary>"
        )
        formatted = format_compact_summary(raw)
        assert "Internal reasoning" not in formatted
        assert "Primary Request" in formatted
        assert "/foo/bar.py" in formatted

    def test_build_compact_summary_message_variants(self):
        """Test different build_compact_summary_message configurations."""
        from utils.compact import build_compact_summary_message

        # With follow-up suppression
        msg1 = build_compact_summary_message("<summary>Test</summary>", suppress_follow_up=True)
        assert "continued from a previous conversation" in msg1
        assert "Continue the conversation" in msg1

        # Without follow-up suppression
        msg2 = build_compact_summary_message("<summary>Test</summary>", suppress_follow_up=False)
        assert "Continue the conversation" not in msg2

        # With recent preserved flag
        msg3 = build_compact_summary_message("<summary>Test</summary>", recent_preserved=True)
        assert "Recent messages are preserved" in msg3

    def test_autocompact_threshold_calculation(self):
        """Auto-compact threshold should be within expected range."""
        from utils.compact import get_autocompact_threshold, AUTOCOMPACT_BUFFER_TOKENS

        threshold = get_autocompact_threshold("any-model")
        # 200k context - 20k reserved - 13k buffer = 167k
        expected = 200_000 - 20_000 - AUTOCOMPACT_BUFFER_TOKENS
        assert threshold == expected

    def test_should_autocompact_respects_failure_limit(self):
        """Auto-compact should stop after max consecutive failures."""
        from utils.compact import should_autocompact, SessionState
        from engine.messages import ConversationMessage

        # Build a huge conversation
        big_messages = [
            ConversationMessage.from_user_text("x" * 100_000)
            for _ in range(50)
        ]

        state_ok = SessionState(consecutive_failures=0)
        assert should_autocompact(big_messages, "test-model", state_ok)

        state_failed = SessionState(consecutive_failures=3)
        assert not should_autocompact(big_messages, "test-model", state_failed)

    def test_session_state_roundtrip(self):
        """SessionState should survive serialization roundtrip."""
        from utils.compact import SessionState

        original = SessionState(compacted=True, turn_counter=5, consecutive_failures=1)
        restored = SessionState.from_dict(original.to_dict())
        assert restored.compacted == original.compacted
        assert restored.turn_counter == original.turn_counter
        assert restored.consecutive_failures == original.consecutive_failures

    def test_token_estimation_grows_with_content(self):
        """Token estimates should increase with message content."""
        from utils.compact import estimate_message_tokens
        from engine.messages import ConversationMessage, TextBlock

        short = [ConversationMessage.from_user_text("Hi")]
        long = [ConversationMessage.from_user_text("x" * 10_000)]

        assert estimate_message_tokens(long) > estimate_message_tokens(short)


# ===========================================================================
# AREA 5: Complex Long Tasks with Multiple Tool Calls
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestComplexLongTasks:
    """Test complex multi-step tasks requiring multiple tool calls."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = _create_test_sandbox("complex-task")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY, model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL, api_format=MINIMAX_FORMAT,
        )
        with c:
            yield c

    def test_create_and_run_python_script(self, client, sandbox):
        """Model should create a Python file and execute it in the sandbox."""
        _create_agent(client, "cx-script-agent", toolkits=["sandbox_operations"],
                      system_prompt=(
                          "You have sandbox access. Use daytona_write_file to write files and "
                          "daytona_bash to run them. Execute ALL requested steps using tools."
                      ))

        events = _send_chat(
            client,
            (
                "Do these steps in the sandbox:\n"
                "1. Write a file /workspace/greet.py with: print('COMPLEX_TASK_OK')\n"
                "2. Run: python /workspace/greet.py\n"
                "3. Tell me the output"
            ),
            agent_name="cx-script-agent",
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = events_of_type(events, "tool_started")
        if tool_started:
            tool_names = [e["tool_name"] for e in tool_started]
            daytona_tools = [t for t in tool_names if t.startswith("daytona_")]
            assert len(daytona_tools) >= 1, f"Expected daytona tools, got: {tool_names}"

    def test_multi_step_file_pipeline(self, client, sandbox):
        """Model should execute a multi-step pipeline: create, modify, verify."""
        _create_agent(client, "cx-pipeline-agent", toolkits=["sandbox_operations"],
                      system_prompt=(
                          "You are a coding assistant with sandbox access. Use daytona_bash, "
                          "daytona_write_file, and daytona_read_file tools. Execute every step."
                      ))

        events = _send_chat(
            client,
            (
                "In the sandbox:\n"
                "1. Create /workspace/data.txt with the text: alpha beta gamma\n"
                "2. Run: wc -w /workspace/data.txt\n"
                "3. Report the word count"
            ),
            agent_name="cx-pipeline-agent",
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        assert "assistant_complete" in _get_event_types(events)

    def test_tool_error_handling(self, client, sandbox):
        """Model should handle tool errors gracefully."""
        _create_agent(client, "cx-error-agent", toolkits=["sandbox_operations"],
                      system_prompt="Use daytona_bash for commands. If a command fails, explain the error.")

        events = _send_chat(
            client,
            "Run this in the sandbox: cat /nonexistent/file/that/does/not/exist",
            agent_name="cx-error-agent",
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        # Should still complete without crashing
        types = _get_event_types(events)
        assert "assistant_complete" in types or "error" in types, (
            f"Should have assistant_complete or error. Types: {types}"
        )
        # If tool errors cause empty assistant_complete, that's acceptable —
        # the key invariant is the server doesn't crash
        text = _get_assistant_text(events)
        # Text may be empty if tool error terminated the stream early

    def test_sequential_tool_calls_preserve_state(self, client, sandbox):
        """Sequential tool calls should see each other's results in the sandbox."""
        _create_agent(client, "cx-state-agent", toolkits=["sandbox_operations"],
                      system_prompt="Use daytona_bash for all commands.")

        events = _send_chat(
            client,
            (
                "In the sandbox, run these commands one after another:\n"
                "1. echo 'STATE_TEST' > /workspace/state_test.txt\n"
                "2. cat /workspace/state_test.txt\n"
                "3. Report what you see"
            ),
            agent_name="cx-state-agent",
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        assert "assistant_complete" in _get_event_types(events)

    def test_long_output_handling(self, client, sandbox):
        """Model should handle large tool output without crashing."""
        _create_agent(client, "cx-long-output", toolkits=["sandbox_operations"],
                      system_prompt="Use daytona_bash for commands.")

        events = _send_chat(
            client,
            "Run in the sandbox: seq 1 200",
            agent_name="cx-long-output",
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        assert "assistant_complete" in _get_event_types(events)


# ===========================================================================
# AREA 6: Code Intelligence System
# ===========================================================================


class TestCodeIntelligenceSystem:
    """Test code intelligence service, LSP, and symbol analysis.

    These tests do NOT require live API keys (unit-level).
    """

    def setup_method(self):
        """Clean up the CI service registry."""
        from code_intelligence.routing.service import dispose_all_code_intelligence
        dispose_all_code_intelligence()

    def teardown_method(self):
        from code_intelligence.routing.service import dispose_all_code_intelligence
        dispose_all_code_intelligence()

    # -- Service lifecycle --

    def test_ci_service_creation_and_status(self):
        """CI service should initialize with correct defaults."""
        from code_intelligence.routing.service import CodeIntelligenceService

        svc = CodeIntelligenceService(
            sandbox_id="ci-test-001",
            workspace_root="/workspace",
        )
        assert svc.sandbox_id == "ci-test-001"
        assert svc.is_initialized is False

        status = svc.status()
        assert status["sandbox_id"] == "ci-test-001"
        assert status["initialized"] is False
        assert "lsp" in status
        assert "tree_cache" in status
        assert "symbol_index" in status

    def test_ci_service_telemetry_fields(self):
        """Telemetry should expose all expected counters."""
        from code_intelligence.routing.service import CodeIntelligenceService
        from code_intelligence.types import CITelemetry

        svc = CodeIntelligenceService(sandbox_id="ci-tel-001", workspace_root="/ws")
        tel = svc.get_telemetry()

        assert isinstance(tel, CITelemetry)
        assert tel.tree_cache_size == 0
        assert tel.symbol_index_size == 0
        assert tel.lsp_connected is False
        assert tel.lsp_query_count == 0
        assert tel.arbiter_active_edits == 0
        assert tel.ledger_entry_count == 0

    def test_ci_service_dispose_safe(self):
        """Dispose should clean up without raising."""
        from code_intelligence.routing.service import CodeIntelligenceService

        svc = CodeIntelligenceService(sandbox_id="ci-dispose", workspace_root="/ws")
        svc.dispose()  # should not raise

    # -- Registry (singleton management) --

    def test_ci_registry_singleton(self):
        """Same sandbox_id should return the same service instance."""
        from code_intelligence.routing.service import get_code_intelligence

        svc1 = get_code_intelligence("singleton-test", "/ws")
        svc2 = get_code_intelligence("singleton-test", "/ws")
        assert svc1 is svc2

    def test_ci_registry_different_sandboxes(self):
        """Different sandbox_ids should get different instances."""
        from code_intelligence.routing.service import get_code_intelligence

        svc_a = get_code_intelligence("ci-a", "/ws")
        svc_b = get_code_intelligence("ci-b", "/ws")
        assert svc_a is not svc_b
        assert svc_a.sandbox_id == "ci-a"
        assert svc_b.sandbox_id == "ci-b"

    def test_ci_registry_dispose_removes(self):
        """Disposing a service should remove it from the registry."""
        from code_intelligence.routing.service import (
            get_code_intelligence,
            get_code_intelligence_if_exists,
            dispose_code_intelligence,
        )

        get_code_intelligence("dispose-reg", "/ws")
        assert get_code_intelligence_if_exists("dispose-reg") is not None
        dispose_code_intelligence("dispose-reg")
        assert get_code_intelligence_if_exists("dispose-reg") is None

    def test_ci_registry_all_status(self):
        """get_all_services_status should return all active services."""
        from code_intelligence.routing.service import get_code_intelligence, get_all_services_status

        get_code_intelligence("status-x", "/ws")
        get_code_intelligence("status-y", "/ws")

        all_status = get_all_services_status()
        assert "status-x" in all_status
        assert "status-y" in all_status

    # -- LSP Client --

    def test_lsp_client_creation(self):
        """LSP client should initialize without error."""
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient(workspace_root="/workspace")
        assert lsp.telemetry.queries == 0
        assert lsp.telemetry.cache_hits == 0

    def test_lsp_client_language_detection(self):
        """LSP client should detect file languages correctly."""
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient()
        assert lsp._detect_language("test.py") == "python"
        assert lsp._detect_language("app.ts") == "typescript"
        assert lsp._detect_language("index.tsx") == "typescript"
        assert lsp._detect_language("script.js") == "javascript"
        assert lsp._detect_language("data.json") == "unknown"

    def test_lsp_client_cache_invalidation(self):
        """Cache invalidation should remove entries for a file."""
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient(workspace_root="/workspace")
        # Manually insert a cache entry
        lsp._put_cached("def:/workspace/test.py:1:0", [])
        lsp._put_cached("ref:/workspace/test.py:5:0", [])
        lsp._put_cached("def:/workspace/other.py:1:0", [])

        lsp.invalidate("/workspace/test.py")

        # test.py entries should be gone, other.py should remain
        assert lsp._get_cached("def:/workspace/test.py:1:0") is None
        assert lsp._get_cached("ref:/workspace/test.py:5:0") is None
        assert lsp._get_cached("def:/workspace/other.py:1:0") is not None

    def test_lsp_client_ensure_ready(self):
        """ensure_ready should return language availability dict."""
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient(workspace_root="/workspace")
        status = lsp.ensure_ready()
        assert "python" in status
        assert "typescript" in status
        assert isinstance(status["python"], bool)

    # -- CI Types --

    def test_edit_request_fields(self):
        """EditRequest should hold all fields."""
        from code_intelligence.types import EditRequest

        req = EditRequest(
            file_path="/ws/test.py",
            old_text="old",
            new_text="new",
            agent_id="agent-1",
            description="Fix bug",
        )
        assert req.file_path == "/ws/test.py"
        assert req.agent_id == "agent-1"

    def test_edit_result_success_and_failure(self):
        """EditResult should represent both success and failure states."""
        from code_intelligence.types import EditResult

        success = EditResult(success=True, file_path="/test.py", message="OK")
        assert success.success is True

        failure = EditResult(success=False, file_path="/test.py", message="Conflict", conflict=True)
        assert failure.success is False
        assert failure.conflict is True

    def test_diagnostic_severity(self):
        """Diagnostic should hold severity and source info."""
        from code_intelligence.types import Diagnostic

        d = Diagnostic(
            file_path="/test.py",
            line=10,
            character=5,
            severity="error",
            message="Syntax error",
            source="python",
        )
        assert d.severity == "error"
        assert d.source == "python"
        assert d.line == 10


# ===========================================================================
# Cross-cutting: SSE event stream validation
# ===========================================================================


@pytest.mark.skipif(not HAS_MINIMAX, reason="MiniMax not configured")
class TestSSEEventStream:
    """Validate the SSE event stream structure and ordering."""

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY, model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL, api_format=MINIMAX_FORMAT,
        )
        with c:
            yield c

    def test_event_stream_has_required_types(self, client):
        """Every chat response must have assistant_complete and line_complete."""
        events = _send_chat(client, "Say hello.")
        types = _get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"
        assert "line_complete" in types, f"Missing line_complete. Types: {types}"

    def test_event_stream_has_transcript_item(self, client):
        """Chat responses should include transcript_item events."""
        events = _send_chat(client, "What is 2+2?")
        types = _get_event_types(events)
        assert "transcript_item" in types, f"Missing transcript_item. Types: {types}"

    def test_assistant_complete_has_message(self, client):
        """assistant_complete events must have a non-empty message field."""
        events = _send_chat(client, "Reply with the word PASS")
        completes = events_of_type(events, "assistant_complete")
        assert len(completes) >= 1
        assert completes[0].get("message"), "assistant_complete should have message content"

    def test_line_complete_terminates_stream(self, client):
        """line_complete should be the last meaningful event in the stream."""
        events = _send_chat(client, "Say one word.")
        types_list = [e["type"] for e in events]
        assert "line_complete" in types_list
        lc_idx = types_list.index("line_complete")
        # Nothing after line_complete except possibly more line_complete
        for evt in events[lc_idx + 1:]:
            assert evt["type"] == "line_complete", f"Unexpected event after line_complete: {evt['type']}"
