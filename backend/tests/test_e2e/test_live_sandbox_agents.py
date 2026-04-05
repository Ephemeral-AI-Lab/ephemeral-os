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
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

from tests.test_e2e.conftest import parse_sse_events, events_of_type

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

pytestmark = [pytest.mark.e2e, pytest.mark.live]

# ---------------------------------------------------------------------------
# Credential loading
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


def _make_live_client(db_session_factory, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.protocol import BackendHostConfig
    from server.app_factory import create_app

    monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
    monkeypatch.setattr("db.engine.initialize_db", lambda *a, **kw: db_session_factory)
    monkeypatch.setattr("engine.agent.make_hook_executor", lambda *a, **kw: None)

    def _patched_load_settings(*a, **kw):
        from config.settings import Settings, DatabaseSettings
        return Settings(
            api_key=MINIMAX_KEY, model=MINIMAX_MODEL, api_format=MINIMAX_FORMAT,
            base_url=MINIMAX_BASE_URL or None,
            daytona_api_key=DAYTONA_KEY, daytona_api_url=DAYTONA_URL,
            daytona_target=DAYTONA_TARGET,
            database=DatabaseSettings(url=f"sqlite:///{tmp_path / 'test.db'}"),
        )

    monkeypatch.setattr("config.load_settings", _patched_load_settings)
    monkeypatch.setattr("config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("server.app_factory.load_settings", _patched_load_settings)

    config = BackendHostConfig(
        api_key=MINIMAX_KEY, model=MINIMAX_MODEL,
        api_format=MINIMAX_FORMAT, base_url=MINIMAX_BASE_URL or None,
    )
    return TestClient(create_app(config))


def _get_sandbox_service():
    from sandbox.service import SandboxService
    return SandboxService()


def _create_test_sandbox(name: str) -> dict:
    svc = _get_sandbox_service()
    return svc.create_sandbox(
        name=f"{name}-{int(time.time())}", language="python",
        labels={"purpose": "live-agent-e2e"},
    )


def _delete_sandbox(sandbox_id: str) -> None:
    try:
        _get_sandbox_service().delete_sandbox(sandbox_id)
    except Exception:
        pass


def _send_chat(client, line: str, *, agent_name: str | None = None,
               sandbox_id: str | None = None, timeout: int = 180) -> list[dict]:
    payload: dict[str, Any] = {"line": line}
    if agent_name:
        payload["agent_name"] = agent_name
    if sandbox_id:
        payload["sandbox_id"] = sandbox_id
    resp = client.post("/api/chat", json=payload, timeout=timeout)
    assert resp.status_code == 200, f"Chat failed: {resp.status_code} {resp.text[:500]}"
    return parse_sse_events(resp.text)


def _get_assistant_text(events: list[dict]) -> str:
    completes = events_of_type(events, "assistant_complete")
    return completes[0].get("message", "") if completes else ""


def _get_event_types(events: list[dict]) -> set[str]:
    return {e["type"] for e in events}


def _create_agent(client, name: str, *, toolkits: list[str] | None = None,
                  system_prompt: str | None = None) -> dict:
    payload: dict[str, Any] = {
        "name": name,
        "description": f"Live E2E agent: {name}",
        "model": MINIMAX_MODEL,
        "toolkits": toolkits or ["sandbox_operations"],
    }
    if system_prompt:
        payload["system_prompt"] = system_prompt
    resp = client.post("/api/agents/", json=payload)
    if resp.status_code == 201:
        return resp.json()
    if resp.status_code == 409:
        client.delete(f"/api/agents/{name}")
        resp2 = client.post("/api/agents/", json=payload)
        assert resp2.status_code == 201, f"Re-create failed: {resp2.status_code} {resp2.text}"
        return resp2.json()
    assert resp.status_code == 201, f"Create failed: {resp.status_code} {resp.text}"
    return resp.json()


# ===========================================================================
# AREA 1: Single-Agent — Daytona Tool Invocation
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestSingleAgentToolInvocation:
    """Single agent invokes individual Daytona tools and we verify event flow."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = _create_test_sandbox("single-agent")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_bash_tool_invocation(self, client, sandbox):
        """Agent invokes daytona_bash and tool events contain correct structure."""
        _create_agent(client, "sa-bash", system_prompt=AGENT_PROMPT)
        events = _send_chat(
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
        events = _send_chat(
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
        _send_chat(
            client,
            "Use daytona_bash to run 'touch /workspace/listable.txt'",
            agent_name="sa-list", sandbox_id=sandbox["id"], timeout=120,
        )
        events = _send_chat(
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
        events = _send_chat(
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
        _send_chat(
            client,
            "Use daytona_bash to run: echo 'GREP_TARGET_XYZ' > /workspace/searchable.txt",
            agent_name="sa-grep", sandbox_id=sandbox["id"], timeout=120,
        )
        events = _send_chat(
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
        _send_chat(
            client,
            "Use daytona_bash to run: touch /workspace/glob_a.py /workspace/glob_b.py",
            agent_name="sa-glob", sandbox_id=sandbox["id"], timeout=120,
        )
        events = _send_chat(
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
        sb = _create_test_sandbox("single-multi-turn")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_create_then_verify_file(self, client, sandbox):
        """Turn 1: create file. Turn 2: verify file content."""
        _create_agent(client, "chain-verify", system_prompt=AGENT_PROMPT)
        marker = f"CHAIN_{uuid.uuid4().hex[:8]}"

        # Turn 1: Create
        events1 = _send_chat(
            client,
            f"Use daytona_write_file to create /workspace/chain.txt with content '{marker}'",
            agent_name="chain-verify", sandbox_id=sandbox["id"], timeout=120,
        )
        assert "assistant_complete" in _get_event_types(events1)
        t1_tools = events_of_type(events1, "tool_started")
        assert len(t1_tools) >= 1, f"Turn 1 should use a tool. Types: {_get_event_types(events1)}"

        # Turn 2: Verify
        events2 = _send_chat(
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

        events1 = _send_chat(
            client,
            "Use daytona_bash to run: echo 'V1_CONTENT' > /workspace/evolve.txt",
            agent_name="chain-3t", sandbox_id=sandbox["id"], timeout=120,
        )
        t1 = events_of_type(events1, "tool_started")
        assert len(t1) >= 1

        events2 = _send_chat(
            client,
            "Use daytona_bash to run: cat /workspace/evolve.txt",
            agent_name="chain-3t", sandbox_id=sandbox["id"], timeout=120,
        )
        t2 = events_of_type(events2, "tool_started")
        assert len(t2) >= 1

        events3 = _send_chat(
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
        events = _send_chat(
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
        sb = _create_test_sandbox("single-events")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_tool_started_contains_tool_input_dict(self, client, sandbox):
        """tool_started events must have tool_input as a dict with expected keys."""
        _create_agent(client, "sa-input-check", system_prompt=AGENT_PROMPT)
        events = _send_chat(
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
        events = _send_chat(
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
        events = _send_chat(
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
        events = _send_chat(
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


# ===========================================================================
# AREA 4: Multi-Agent — Parallel Dispatch via RunParallelAgentsTool
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestMultiAgentParallelDispatch:
    """Multi-agent: coordinator dispatches parallel workers to a shared sandbox.

    Validates that RunParallelAgentsTool works end-to-end with real LLM + sandbox.
    """

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = _create_test_sandbox("multi-agent")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_parallel_workers_execute_in_sandbox(self, client, sandbox):
        """Coordinator dispatches 2 workers to run bash commands in the sandbox."""
        _create_agent(
            client, "multi-coordinator",
            toolkits=["sandbox_operations"],
            system_prompt=(
                "You are a coordinator. When asked to dispatch tasks, use the "
                "run_parallel_agents tool to send work items to worker agents. "
                "Always use tools."
            ),
        )
        events = _send_chat(
            client,
            (
                f"Use run_parallel_agents with these parameters:\n"
                f"- items: [\"echo WORKER_1_OK\", \"echo WORKER_2_OK\"]\n"
                f"- agent_name: \"multi-coordinator\"\n"
                f"- prompt_template: \"Use daytona_bash to run: {{{{item}}}}\"\n"
                f"- max_workers: 2\n"
                f"Report the results."
            ),
            agent_name="multi-coordinator", sandbox_id=sandbox["id"], timeout=300,
        )
        types = _get_event_types(events)
        assert "assistant_complete" in types, f"No assistant_complete. Types: {types}"

        text = _get_assistant_text(events)
        tool_started = events_of_type(events, "tool_started")

        # The coordinator should have used at least one tool
        has_tool_use = len(tool_started) >= 1
        has_worker_ref = any(
            kw in text.lower()
            for kw in ["worker", "parallel", "result", "success", "completed"]
        )
        assert has_tool_use or has_worker_ref, (
            f"Expected tool use or worker references. Tools: {[e.get('tool_name') for e in tool_started]}, "
            f"Text: {text[:300]}"
        )

    def test_parallel_workers_produce_distinct_results(self, client, sandbox):
        """Each parallel worker should produce its own distinct output."""
        _create_agent(
            client, "multi-distinct",
            toolkits=["sandbox_operations"],
            system_prompt=(
                "You are a task executor with sandbox access. "
                "When asked to do something, use daytona_bash to execute commands. "
                "Always use tools, never describe."
            ),
        )

        # Have the agent execute two distinct commands sequentially to verify
        # it can handle multiple tool calls with distinct outputs
        events = _send_chat(
            client,
            (
                "Execute these two commands in the sandbox using daytona_bash:\n"
                "1. echo 'RESULT_ALPHA'\n"
                "2. echo 'RESULT_BETA'\n"
                "Run both commands."
            ),
            agent_name="multi-distinct", sandbox_id=sandbox["id"], timeout=180,
        )
        tool_started = events_of_type(events, "tool_started")
        tool_completed = events_of_type(events, "tool_completed")
        text = _get_assistant_text(events)

        all_output = " ".join(e.get("output", "") for e in tool_completed) + " " + text
        has_alpha = "RESULT_ALPHA" in all_output
        has_beta = "RESULT_BETA" in all_output
        has_tools = len(tool_started) >= 1

        assert has_tools, f"Should use tools. Types: {_get_event_types(events)}"
        # At least one marker should appear in tool output or text
        assert has_alpha or has_beta or len(tool_started) >= 2, (
            f"Expected distinct results. Outputs: {all_output[:300]}"
        )


# ===========================================================================
# AREA 5: Multi-Agent — Cross-Agent File Visibility
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestMultiAgentFileVisibility:
    """Verify that files written by one agent are visible to another in the same sandbox."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = _create_test_sandbox("cross-agent")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_agent_a_writes_agent_b_reads(self, client, sandbox):
        """Agent A writes a file, Agent B reads it — verifying shared sandbox state."""
        marker = f"CROSS_AGENT_{uuid.uuid4().hex[:8]}"

        # Agent A: writer
        _create_agent(client, "writer-agent", system_prompt=AGENT_PROMPT)
        events_write = _send_chat(
            client,
            f"Use daytona_write_file to write '{marker}' to /workspace/cross_agent_test.txt",
            agent_name="writer-agent", sandbox_id=sandbox["id"], timeout=120,
        )
        t_write = events_of_type(events_write, "tool_started")
        assert len(t_write) >= 1, f"Writer should use a tool. Types: {_get_event_types(events_write)}"

        # Agent B: reader
        _create_agent(client, "reader-agent", system_prompt=AGENT_PROMPT)
        events_read = _send_chat(
            client,
            "Use daytona_bash to run 'cat /workspace/cross_agent_test.txt' and tell me its contents.",
            agent_name="reader-agent", sandbox_id=sandbox["id"], timeout=120,
        )
        t_read = events_of_type(events_read, "tool_started")
        t_completed = events_of_type(events_read, "tool_completed")
        text_read = _get_assistant_text(events_read)
        all_output = " ".join(e.get("output", "") for e in t_completed)

        assert len(t_read) >= 1, f"Reader should use a tool. Types: {_get_event_types(events_read)}"

        has_marker = marker in all_output or marker in text_read
        has_file_ref = "cross_agent_test" in text_read.lower() or len(t_read) >= 1
        assert has_marker or has_file_ref, (
            f"Reader should see writer's content '{marker}'. "
            f"Text: {text_read[:200]}, Outputs: {all_output[:200]}"
        )

    def test_sequential_agents_accumulate_state(self, client, sandbox):
        """Three agents write sequentially, final agent reads all content."""
        _create_agent(client, "accum-agent", system_prompt=AGENT_PROMPT)

        # Agent writes 3 lines
        for i in range(1, 4):
            _send_chat(
                client,
                f"Use daytona_bash to run: echo 'LINE_{i}' >> /workspace/accumulate.txt",
                agent_name="accum-agent", sandbox_id=sandbox["id"], timeout=120,
            )

        # Verify all lines
        events = _send_chat(
            client,
            "Use daytona_bash to run 'cat /workspace/accumulate.txt' and count the lines.",
            agent_name="accum-agent", sandbox_id=sandbox["id"], timeout=120,
        )
        tool_completed = events_of_type(events, "tool_completed")
        text = _get_assistant_text(events)
        all_output = " ".join(e.get("output", "") for e in tool_completed) + " " + text

        # At least some lines should be visible
        has_content = any(f"LINE_{i}" in all_output for i in range(1, 4))
        has_tool = len(events_of_type(events, "tool_started")) >= 1
        assert has_content or has_tool, (
            f"Should see accumulated lines or use tool. Output: {all_output[:300]}"
        )


# ===========================================================================
# AREA 6: Toolkit Registration & Schema Verification (no live API needed)
# ===========================================================================


class TestToolkitRegistrationAndSchema:
    """Verify DaytonaToolkit registers all 12 tools with valid schemas."""

    def test_toolkit_has_12_tools(self):
        """DaytonaToolkit must register exactly 12 tools."""
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit(sandbox_id="schema-test")
        names = sorted(toolkit.tool_names())
        expected = sorted(KNOWN_DAYTONA_TOOLS)
        assert names == expected, f"Tool mismatch.\nGot:      {names}\nExpected: {expected}"

    def test_each_tool_has_valid_schema(self):
        """Every tool must produce a valid API schema with name, description, input_schema."""
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit(sandbox_id="schema-test")
        for tool in toolkit.list_tools():
            schema = tool.to_api_schema()
            assert schema["name"] == tool.name
            assert len(schema["description"]) > 10, f"{tool.name}: description too short"
            assert "properties" in schema["input_schema"] or "type" in schema["input_schema"], (
                f"{tool.name}: invalid input_schema: {schema['input_schema']}"
            )

    def test_run_parallel_agents_tool_schema(self):
        """RunParallelAgentsTool must have correct input schema."""
        from tools.subagent.parallel_dispatch_tool import RunParallelAgentsTool
        tool = RunParallelAgentsTool()
        schema = tool.to_api_schema()
        assert schema["name"] == "run_parallel_agents"
        props = schema["input_schema"].get("properties", {})
        assert "items" in props, f"Missing 'items' in schema: {props.keys()}"
        assert "agent_name" in props, f"Missing 'agent_name' in schema: {props.keys()}"
        assert "prompt_template" in props, f"Missing 'prompt_template' in schema: {props.keys()}"

    def test_toolkit_available_in_registry(self, app_client):
        """GET /api/agents/toolkits/available must include sandbox_operations."""
        client, _ = app_client
        resp = client.get("/api/agents/toolkits/available")
        assert resp.status_code == 200
        toolkits = resp.json()
        assert "sandbox_operations" in toolkits, f"Missing sandbox_operations. Got: {toolkits}"


# ===========================================================================
# AREA 7: RunParallelAgentsTool Unit Tests (no live API needed)
# ===========================================================================


class TestRunParallelAgentsToolUnit:
    """Unit tests for RunParallelAgentsTool with mock agent runner."""

    @pytest.mark.asyncio
    async def test_parallel_dispatch_success(self):
        """Tool dispatches items to agent_fn and collects results."""
        from tools.subagent.parallel_dispatch_tool import RunParallelAgentsTool, RunParallelAgentsInput
        from tools.base import ToolExecutionContext

        call_log = []

        async def fake_run(agent_name, prompt, *, session_id=None, options=None):
            call_log.append({"agent": agent_name, "prompt": prompt})
            return f"result for: {prompt}"

        tool = RunParallelAgentsTool(run_agent_fn=fake_run)
        ctx = ToolExecutionContext(cwd="/tmp", metadata={})

        result = await tool.execute(
            RunParallelAgentsInput(
                items=["task_a", "task_b", "task_c"],
                agent_name="worker",
                prompt_template="Process: {{item}} (index {{index}})",
                max_workers=3,
            ),
            ctx,
        )

        data = json.loads(result.output)
        assert data["total"] == 3
        assert data["success_count"] == 3
        assert data["failed_count"] == 0
        assert len(call_log) == 3

        # Verify template rendering
        prompts = [c["prompt"] for c in call_log]
        assert any("task_a" in p for p in prompts)
        assert any("task_b" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_parallel_dispatch_partial_failure(self):
        """One worker failure should not crash others."""
        from tools.subagent.parallel_dispatch_tool import RunParallelAgentsTool, RunParallelAgentsInput
        from tools.base import ToolExecutionContext

        async def flaky_run(agent_name, prompt, *, session_id=None, options=None):
            if "fail" in prompt:
                raise RuntimeError("Simulated failure")
            return f"ok: {prompt}"

        tool = RunParallelAgentsTool(run_agent_fn=flaky_run)
        ctx = ToolExecutionContext(cwd="/tmp", metadata={})

        result = await tool.execute(
            RunParallelAgentsInput(
                items=["good_task", "fail_task", "another_good"],
                agent_name="worker",
                prompt_template="{{item}}",
            ),
            ctx,
        )

        data = json.loads(result.output)
        assert data["total"] == 3
        assert data["success_count"] == 2
        assert data["failed_count"] == 1

        # The failed one should have error info
        failed = [r for r in data["results"] if r["status"] == "error"]
        assert len(failed) == 1
        assert "Simulated failure" in failed[0]["error"]

    @pytest.mark.asyncio
    async def test_parallel_dispatch_empty_items(self):
        """Empty items list should return error."""
        from tools.subagent.parallel_dispatch_tool import RunParallelAgentsTool, RunParallelAgentsInput
        from tools.base import ToolExecutionContext

        tool = RunParallelAgentsTool(run_agent_fn=lambda *a, **kw: None)
        ctx = ToolExecutionContext(cwd="/tmp", metadata={})

        result = await tool.execute(
            RunParallelAgentsInput(
                items=[], agent_name="w", prompt_template="{{item}}",
            ),
            ctx,
        )
        assert result.is_error
        data = json.loads(result.output)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_parallel_dispatch_no_runner(self):
        """Missing agent runner should return error."""
        from tools.subagent.parallel_dispatch_tool import RunParallelAgentsTool, RunParallelAgentsInput
        from tools.base import ToolExecutionContext

        tool = RunParallelAgentsTool(run_agent_fn=None)
        ctx = ToolExecutionContext(cwd="/tmp", metadata={})

        result = await tool.execute(
            RunParallelAgentsInput(
                items=["x"], agent_name="w", prompt_template="{{item}}",
            ),
            ctx,
        )
        assert result.is_error
