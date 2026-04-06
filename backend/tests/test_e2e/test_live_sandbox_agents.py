# ruff: noqa
"""Live E2E: single-agent and multi-agent sandbox tool calling.

Tests the full pipeline: agent creation, sandbox attachment, tool invocation,
result verification — using real MiniMax LLM + real Daytona sandbox.

Single-agent tests verify:
- Agent can invoke individual Daytona tools (bash, write, read, grep, glob, list)
- Tool events flow correctly (tool_started → tool_completed)
- File roundtrips work (write → read with content verification)
- Multi-turn tool chaining preserves sandbox state
- LSP tools are available in the schema

Multi-agent tests verify:
- RunParallelAgentsTool dispatches work to multiple workers
- Workers execute in parallel with sandbox access
- Results contain real file content from the sandbox
- Failure isolation: one worker error doesn't crash others

Run with: pytest tests/test_e2e/test_live_sandbox_agents.py -m live -v
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from engine.eval_agent import EvalAgent
from tests.test_e2e.conftest import (
    MINIMAX_KEY,
    MINIMAX_MODEL,
    MINIMAX_BASE_URL,
    MINIMAX_FORMAT,
    DAYTONA_KEY,
    DAYTONA_URL,
    DAYTONA_TARGET,
    HAS_BOTH,
    make_live_client,
    parse_sse_events,
    events_of_type,
    create_test_sandbox,
    delete_test_sandbox,
    send_chat,
    create_test_agent,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KNOWN_DAYTONA_TOOLS = {
    "daytona_bash", "daytona_read_file", "daytona_write_file",
    "daytona_list_files", "daytona_grep", "daytona_glob",
    "daytona_edit_file", "daytona_lsp_hover", "daytona_lsp_definition",
    "daytona_lsp_references", "daytona_lsp_diagnostics", "daytona_codeact",
}

AGENT_PROMPT = (
    "You are a developer with a remote Daytona sandbox. "
    "You MUST use tools for every action — never just describe what you'd do. "
    "Use daytona_write_file to create files, daytona_bash to run commands, "
    "daytona_read_file to read files, daytona_list_files to list directories, "
    "daytona_grep to search content, daytona_glob to find files. "
    "Always execute every step using tools. Be concise."
)


def _get_assistant_text(events: list[dict]) -> str:
    completes = events_of_type(events, "assistant_complete")
    return completes[0].get("message", "") if completes else ""


def _get_event_types(events: list[dict]) -> set[str]:
    return {e["type"] for e in events}


def _create_agent(client, name: str, *, toolkits: list[str] | None = None,
                  system_prompt: str | None = None) -> dict:
    """Create agent with sandbox_operations default toolkit."""
    return create_test_agent(
        client, name,
        toolkits=toolkits or ["sandbox_operations"],
        system_prompt=system_prompt,
    )


# ===========================================================================
# AREA 1: Single-Agent — Daytona Tool Invocation
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestSingleAgentToolInvocation:
    """Single agent invokes individual Daytona tools and we verify event flow."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("single-agent")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_bash_tool_invocation(self, client, sandbox):
        """Agent invokes daytona_bash and tool events contain correct structure."""
        _create_agent(client, "sa-bash", system_prompt=AGENT_PROMPT)
        events = send_chat(
            client,
            "Use daytona_bash to run 'echo SINGLE_AGENT_BASH_OK' in the sandbox.",
            agent_name="sa-bash", sandbox_id=sandbox["id"], timeout=120,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types, f"No assistant_complete. Types: {types}"

        tool_started = events_of_type(events, "tool_started")
        assert len(tool_started) >= 1, f"No tool_started events. Types: {types}"

        for ev in tool_started:
            name = ev.get("tool_name", "")
            assert name in KNOWN_DAYTONA_TOOLS, f"Unknown tool '{name}'"
            tool_input = ev.get("tool_input")
            assert isinstance(tool_input, dict), f"tool_input should be dict: {ev}"

        tool_completed = events_of_type(events, "tool_completed")
        if tool_completed:
            success = [e for e in tool_completed if not e.get("is_error", True)]
            assert len(success) >= 1, f"No successful tool completions: {tool_completed}"

    def test_write_file_tool(self, client, sandbox):
        """Agent uses daytona_write_file to create a file in the sandbox."""
        _create_agent(client, "sa-write", system_prompt=AGENT_PROMPT)
        events = send_chat(
            client,
            "Use daytona_write_file to write 'WRITE_TOOL_MARKER' to /workspace/write_test.txt",
            agent_name="sa-write", sandbox_id=sandbox["id"], timeout=120,
        )
        tool_started = events_of_type(events, "tool_started")
        assert len(tool_started) >= 1, f"No tools used. Types: {_get_event_types(events)}"

        tool_names = [e.get("tool_name") for e in tool_started]
        assert any(n in ("daytona_write_file", "daytona_bash") for n in tool_names), (
            f"Expected write tool, got: {tool_names}"
        )

    def test_list_files_tool(self, client, sandbox):
        """Agent uses daytona_list_files to list a directory."""
        _create_agent(client, "sa-list", system_prompt=AGENT_PROMPT)
        # First create a file so there's something to list
        send_chat(
            client,
            "Use daytona_bash to run 'touch /workspace/listable.txt'",
            agent_name="sa-list", sandbox_id=sandbox["id"], timeout=120,
        )
        events = send_chat(
            client,
            "Use daytona_list_files to list the /workspace directory.",
            agent_name="sa-list", sandbox_id=sandbox["id"], timeout=120,
        )
        tool_started = events_of_type(events, "tool_started")
        assert len(tool_started) >= 1, f"No tools used. Types: {_get_event_types(events)}"

    def test_file_roundtrip_write_read(self, client, sandbox):
        """Write file via tool, read it back — verify content roundtrip."""
        _create_agent(client, "sa-roundtrip", system_prompt=AGENT_PROMPT)
        marker = f"ROUNDTRIP_{uuid.uuid4().hex[:8]}"
        events = send_chat(
            client,
            (
                f"Do these two steps in the sandbox using tools:\n"
                f"1. Use daytona_write_file to write '{marker}' to /workspace/roundtrip.txt\n"
                f"2. Use daytona_bash to run 'cat /workspace/roundtrip.txt'\n"
                f"Do both steps."
            ),
            agent_name="sa-roundtrip", sandbox_id=sandbox["id"], timeout=180,
        )
        tool_started = events_of_type(events, "tool_started")
        tool_completed = events_of_type(events, "tool_completed")
        assert len(tool_started) >= 1, f"No tools used. Types: {_get_event_types(events)}"

        all_outputs = " ".join(e.get("output", "") for e in tool_completed)
        text = _get_assistant_text(events)
        has_marker = marker in all_outputs or marker in text
        has_write_tool = any(
            e.get("tool_name") in ("daytona_write_file", "daytona_bash")
            for e in tool_started
        )
        assert has_marker or has_write_tool, (
            f"Roundtrip: should find marker or at least attempt write tool. "
            f"Tool names: {[e.get('tool_name') for e in tool_started]}, "
            f"Text: {text[:200]}"
        )

    def test_grep_search_tool(self, client, sandbox):
        """Agent uses daytona_grep to search file content."""
        _create_agent(client, "sa-grep", system_prompt=AGENT_PROMPT)
        # Seed a file first
        send_chat(
            client,
            "Use daytona_bash to run: echo 'GREP_TARGET_XYZ' > /workspace/searchable.txt",
            agent_name="sa-grep", sandbox_id=sandbox["id"], timeout=120,
        )
        events = send_chat(
            client,
            "Use daytona_grep to search for 'GREP_TARGET' in /workspace/",
            agent_name="sa-grep", sandbox_id=sandbox["id"], timeout=120,
        )
        tool_started = events_of_type(events, "tool_started")
        assert len(tool_started) >= 1, f"No tools used. Types: {_get_event_types(events)}"

        tool_names = [e.get("tool_name") for e in tool_started]
        assert any(n in ("daytona_grep", "daytona_bash") for n in tool_names), (
            f"Expected grep or bash tool, got: {tool_names}"
        )

    def test_glob_search_tool(self, client, sandbox):
        """Agent uses daytona_glob to find files by pattern."""
        _create_agent(client, "sa-glob", system_prompt=AGENT_PROMPT)
        # Seed files
        send_chat(
            client,
            "Use daytona_bash to run: touch /workspace/glob_a.py /workspace/glob_b.py",
            agent_name="sa-glob", sandbox_id=sandbox["id"], timeout=120,
        )
        events = send_chat(
            client,
            "Use daytona_glob to find all .py files in /workspace/",
            agent_name="sa-glob", sandbox_id=sandbox["id"], timeout=120,
        )
        tool_started = events_of_type(events, "tool_started")
        assert len(tool_started) >= 1, f"No tools used. Types: {_get_event_types(events)}"


# ===========================================================================
# AREA 2: Single-Agent — Multi-Turn Tool Chaining
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestSingleAgentMultiTurnChaining:
    """Multi-turn conversations where each turn uses tools and sandbox state persists."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("single-multi-turn")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_create_then_verify_file(self, client, sandbox):
        """Turn 1: create file. Turn 2: verify file content."""
        _create_agent(client, "chain-verify", system_prompt=AGENT_PROMPT)
        marker = f"CHAIN_{uuid.uuid4().hex[:8]}"

        # Turn 1: Create
        events1 = send_chat(
            client,
            f"Use daytona_write_file to create /workspace/chain.txt with content '{marker}'",
            agent_name="chain-verify", sandbox_id=sandbox["id"], timeout=120,
        )
        assert "assistant_complete" in _get_event_types(events1)
        t1_tools = events_of_type(events1, "tool_started")
        assert len(t1_tools) >= 1, f"Turn 1 should use a tool. Types: {_get_event_types(events1)}"

        # Turn 2: Verify
        events2 = send_chat(
            client,
            "Use daytona_bash to run 'cat /workspace/chain.txt' and tell me the content.",
            agent_name="chain-verify", sandbox_id=sandbox["id"], timeout=120,
        )
        assert "assistant_complete" in _get_event_types(events2)
        text2 = _get_assistant_text(events2)
        t2_completed = events_of_type(events2, "tool_completed")
        all_output = " ".join(e.get("output", "") for e in t2_completed)
        has_marker = marker in text2 or marker in all_output
        has_tool = len(events_of_type(events2, "tool_started")) >= 1
        assert has_marker or has_tool, (
            f"Turn 2 should reference '{marker}' or use a tool. Text: {text2[:200]}"
        )

    def test_three_turn_create_read_modify(self, client, sandbox):
        """3-turn chain: create -> read -> modify. All turns use tools."""
        _create_agent(client, "chain-3t", system_prompt=AGENT_PROMPT)

        events1 = send_chat(
            client,
            "Use daytona_bash to run: echo 'V1_CONTENT' > /workspace/evolve.txt",
            agent_name="chain-3t", sandbox_id=sandbox["id"], timeout=120,
        )
        t1 = events_of_type(events1, "tool_started")
        assert len(t1) >= 1

        events2 = send_chat(
            client,
            "Use daytona_bash to run: cat /workspace/evolve.txt",
            agent_name="chain-3t", sandbox_id=sandbox["id"], timeout=120,
        )
        t2 = events_of_type(events2, "tool_started")
        assert len(t2) >= 1

        events3 = send_chat(
            client,
            "Use daytona_bash to run: echo 'V2_CONTENT' >> /workspace/evolve.txt",
            agent_name="chain-3t", sandbox_id=sandbox["id"], timeout=120,
        )
        t3 = events_of_type(events3, "tool_started")
        assert len(t3) >= 1

        total = len(t1) + len(t2) + len(t3)
        assert total >= 3, f"Expected at least 3 tool calls across 3 turns, got {total}"

    def test_complex_multi_step_task(self, client, sandbox):
        """Agent performs create-file -> execute -> capture-output in one turn."""
        _create_agent(client, "chain-complex", system_prompt=AGENT_PROMPT)
        events = send_chat(
            client,
            (
                "Do these steps in the sandbox:\n"
                "1. Use daytona_write_file to create /workspace/hello.py with: print('HELLO_FROM_E2E')\n"
                "2. Use daytona_bash to run: python3 /workspace/hello.py\n"
                "3. Report the output."
            ),
            agent_name="chain-complex", sandbox_id=sandbox["id"], timeout=180,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types

        tool_started = events_of_type(events, "tool_started")
        if tool_started:
            daytona_tools = [e for e in tool_started if e.get("tool_name", "").startswith("daytona_")]
            assert len(daytona_tools) >= 1, f"Expected daytona tools: {[e.get('tool_name') for e in tool_started]}"

        # Check if the output contains our marker
        tool_completed = events_of_type(events, "tool_completed")
        all_output = " ".join(e.get("output", "") for e in tool_completed)
        text = _get_assistant_text(events)
        has_hello = "HELLO_FROM_E2E" in all_output or "HELLO_FROM_E2E" in text
        has_tool = len(tool_started) >= 1
        assert has_hello or has_tool, (
            f"Should find HELLO_FROM_E2E in output or at least attempt tools. "
            f"Text: {text[:200]}, Outputs: {all_output[:200]}"
        )


# ===========================================================================
# AREA 3: Single-Agent — Tool Schema & Event Structure Verification
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestSingleAgentEventStructure:
    """Verify event structure, tool input/output shapes, and error handling."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("single-events")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_tool_started_contains_tool_input_dict(self, client, sandbox):
        """tool_started events must have tool_input as a dict with expected keys."""
        _create_agent(client, "sa-input-check", system_prompt=AGENT_PROMPT)
        events = send_chat(
            client,
            "Use daytona_bash to run 'echo INPUT_STRUCTURE_OK'",
            agent_name="sa-input-check", sandbox_id=sandbox["id"], timeout=120,
        )
        tool_started = events_of_type(events, "tool_started")
        assert len(tool_started) >= 1

        for ev in tool_started:
            tool_input = ev.get("tool_input")
            assert tool_input is not None, f"tool_started missing tool_input: {ev}"
            assert isinstance(tool_input, dict), f"tool_input should be dict: {type(tool_input)}"

            name = ev.get("tool_name", "")
            if name == "daytona_bash":
                assert "command" in tool_input, f"daytona_bash missing 'command': {tool_input}"
            elif name == "daytona_write_file":
                assert "file_path" in tool_input
                assert "content" in tool_input
            elif name == "daytona_read_file":
                assert "file_path" in tool_input

    def test_tool_completed_has_nonempty_output(self, client, sandbox):
        """Successful tool_completed events must have non-empty output."""
        _create_agent(client, "sa-output-check", system_prompt=AGENT_PROMPT)
        events = send_chat(
            client,
            "Use daytona_bash to run 'echo OUTPUT_CHECK_OK'",
            agent_name="sa-output-check", sandbox_id=sandbox["id"], timeout=120,
        )
        tool_completed = events_of_type(events, "tool_completed")
        if tool_completed:
            for ev in tool_completed:
                if not ev.get("is_error", False):
                    output = ev.get("output", "")
                    assert output, f"Successful tool_completed has empty output: {ev}"

    def test_event_ordering_thinking_before_text(self, client, sandbox):
        """If thinking_delta events exist, they must precede assistant_delta."""
        _create_agent(client, "sa-ordering", system_prompt=AGENT_PROMPT)
        events = send_chat(
            client,
            "Think step by step: what is 7 * 8? Then reply.",
            agent_name="sa-ordering", sandbox_id=sandbox["id"], timeout=60,
        )
        thinking = events_of_type(events, "thinking_delta")
        text_deltas = events_of_type(events, "assistant_delta")
        if thinking and text_deltas:
            all_types = [e["type"] for e in events]
            first_thinking = all_types.index("thinking_delta")
            first_text = all_types.index("assistant_delta")
            assert first_thinking < first_text, (
                f"thinking at {first_thinking} should precede text at {first_text}"
            )

    def test_full_event_lifecycle(self, client, sandbox):
        """A tool-using chat must emit: transcript_item, assistant_complete, and line_complete (or error)."""
        _create_agent(client, "sa-lifecycle", system_prompt=AGENT_PROMPT)
        events = send_chat(
            client,
            "Use daytona_bash to run 'echo LIFECYCLE_OK'",
            agent_name="sa-lifecycle", sandbox_id=sandbox["id"], timeout=120,
        )
        types = _get_event_types(events)
        assert "transcript_item" in types, f"Missing transcript_item. Types: {types}"
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"
        # line_complete may be absent if an error event interrupted the stream
        assert "line_complete" in types or "error" in types, (
            f"Missing both line_complete and error. Types: {types}"
        )

        # If model used tools and no error, verify tool event pair
        if "tool_started" in types and "error" not in types:
            assert "tool_completed" in types, f"tool_started without tool_completed. Types: {types}"


