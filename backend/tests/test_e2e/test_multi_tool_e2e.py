# ruff: noqa
"""E2E tests for multiple tool calling scenarios.

Tests verify the agent loop handles multiple tool calls correctly.

Requires live MiniMax API + Daytona sandbox.
Run with: pytest backend/tests/test_e2e/test_multi_tool_e2e.py -m live -v
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

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

pytestmark = [pytest.mark.e2e, pytest.mark.live]


def _load_settings() -> dict:
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}


_SETTINGS = _load_settings()

MINIMAX_KEY = os.environ.get("MINIMAX_API_KEY") or _SETTINGS.get("api_key", "")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL") or _SETTINGS.get("model", "MiniMax-M2.7-highspeed")
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL") or _SETTINGS.get("base_url", "")
MINIMAX_FORMAT = os.environ.get("MINIMAX_API_FORMAT") or _SETTINGS.get("api_format", "openai")

DAYTONA_KEY = os.environ.get("DAYTONA_API_KEY") or _SETTINGS.get("daytona_api_key", "")
DAYTONA_URL = os.environ.get("DAYTONA_API_URL") or _SETTINGS.get("daytona_api_url", "")
DAYTONA_TARGET = os.environ.get("DAYTONA_TARGET") or _SETTINGS.get("daytona_target", "")

HAS_MINIMAX = bool(MINIMAX_KEY and MINIMAX_BASE_URL)
HAS_DAYTONA = bool(DAYTONA_KEY and DAYTONA_URL)
HAS_BOTH = HAS_MINIMAX and HAS_DAYTONA


def _make_live_client(
    db_session_factory, tmp_path, monkeypatch, *, api_key, model, base_url, api_format
):
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


def _create_test_sandbox(name: str = "e2e-multi-tool") -> dict:
    svc = _get_sandbox_service()
    sandbox = svc.create_sandbox(
        name=f"{name}-{int(time.time())}",
        language="python",
        labels={"purpose": "e2e-multi-tool"},
    )
    return sandbox


def _delete_sandbox(sandbox_id: str) -> None:
    try:
        svc = _get_sandbox_service()
        svc.delete_sandbox(sandbox_id)
    except Exception:
        pass


def _send_chat(
    client,
    line: str,
    *,
    agent_name: str | None = None,
    sandbox_id: str | None = None,
) -> list[dict]:
    payload: dict[str, Any] = {"line": line}
    if agent_name:
        payload["agent_name"] = agent_name
    if sandbox_id:
        payload["sandbox_id"] = sandbox_id

    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 200, f"Chat failed: {resp.status_code} {resp.text[:500]}"
    return parse_sse_events(resp.text)


def _get_assistant_text(events: list[dict]) -> str:
    completes = events_of_type(events, "assistant_complete")
    if completes:
        return completes[0].get("message", "")
    return ""


def _get_event_types(events: list[dict]) -> set[str]:
    return {e["type"] for e in events}


def _get_tool_started_events(events: list[dict]) -> list[dict]:
    return events_of_type(events, "tool_started")


def _get_tool_completed_events(events: list[dict]) -> list[dict]:
    return events_of_type(events, "tool_completed")


def _create_agent(
    client,
    name: str,
    *,
    toolkits: list[str] | None = None,
    skills: list[str] | None = None,
    system_prompt: str | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "name": name,
        "description": f"E2E multi-tool test agent: {name}",
        "model": MINIMAX_MODEL,
    }
    if toolkits:
        payload["toolkits"] = toolkits
    if skills:
        payload["skills"] = skills
    if system_prompt:
        payload["system_prompt"] = system_prompt

    resp = client.post("/api/agents/", json=payload)
    if resp.status_code == 201:
        return resp.json()
    get_resp = client.get(f"/api/agents/{name}")
    if get_resp.status_code == 200:
        return get_resp.json()
    assert False, f"Failed to create or get agent '{name}': {resp.status_code} {resp.text}"


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestMultipleToolCalls:
    """Test multiple tool calls - verify agent makes multiple calls."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = _create_test_sandbox("multi-tool")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(
            db_session_factory,
            tmp_path,
            monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with c:
            yield c

    def test_agent_makes_multiple_tool_calls(self, client, sandbox):
        """Agent should make multiple tool calls in one turn."""
        _create_agent(
            client,
            "multi-call-agent",
            toolkits=["sandbox_operations"],
            system_prompt="Make multiple tool calls to complete the task.",
        )

        events = _send_chat(
            client,
            (
                "1. Create /workspace/multi1.txt with 'MULTI1'\n"
                "2. Create /workspace/multi2.txt with 'MULTI2'\n"
                "3. Run: echo 'MULTI_DONE'"
            ),
            agent_name="multi-call-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]

        daytona_calls = [n for n in tool_names if n.startswith("daytona_")]
        assert len(daytona_calls) >= 2, (
            f"Should make at least 2 tool calls. Got {len(daytona_calls)}: {daytona_calls}"
        )

    def test_write_then_bash_sequential(self, client, sandbox):
        """Write then bash - verify order."""
        _create_agent(
            client,
            "write-bash-agent",
            toolkits=["sandbox_operations"],
            system_prompt="Write the file first, then run a command to read it.",
        )

        events = _send_chat(
            client,
            (
                "1. Create /workspace/seq_test.txt with 'SEQUENTIAL_TEST'\n"
                "2. Run: cat /workspace/seq_test.txt"
            ),
            agent_name="write-bash-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]

        has_write = "daytona_write_file" in tool_names
        has_bash = "daytona_bash" in tool_names

        assert has_write or has_bash, (
            f"Should use daytona_write_file or daytona_bash. Tools: {tool_names}"
        )

        if has_write and has_bash:
            write_idx = tool_names.index("daytona_write_file")
            bash_idx = tool_names.index("daytona_bash")
            assert write_idx < bash_idx, f"Write should come before bash. Order: {tool_names}"

    def test_multiple_bash_commands(self, client, sandbox):
        """Multiple bash commands in same turn."""
        _create_agent(
            client,
            "multi-bash-agent",
            toolkits=["sandbox_operations"],
            system_prompt="Run all three echo commands.",
        )

        events = _send_chat(
            client,
            "Run: echo 'CMD_1'\nRun: echo 'CMD_2'\nRun: echo 'CMD_3'",
            agent_name="multi-bash-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]

        daytona_bash_count = tool_names.count("daytona_bash")
        assert daytona_bash_count >= 2, (
            f"Should have at least 2 bash calls. Got {daytona_bash_count}. Tools: {tool_names}"
        )

    def test_event_ordering_correct(self, client, sandbox):
        """Tool started should come before tool completed."""
        _create_agent(
            client,
            "order-agent",
            toolkits=["sandbox_operations"],
            system_prompt="Execute the command.",
        )

        events = _send_chat(
            client,
            "Create /workspace/order_test.txt with content: 'ORDER_TEST'",
            agent_name="order-agent",
            sandbox_id=sandbox["id"],
        )

        event_types = [e["type"] for e in events]
        started_indices = [i for i, t in enumerate(event_types) if t == "tool_started"]
        completed_indices = [i for i, t in enumerate(event_types) if t == "tool_completed"]

        if started_indices and completed_indices:
            assert started_indices[0] < completed_indices[0], (
                f"Started (idx={started_indices[0]}) should come before "
                f"completed (idx={completed_indices[0]})"
            )

    def test_agent_uses_different_tools(self, client, sandbox):
        """Agent should use different tools for different purposes."""
        _create_agent(
            client,
            "diff-tools-agent",
            toolkits=["sandbox_operations"],
            system_prompt="Use different tools as needed.",
        )

        events = _send_chat(
            client,
            (
                "1. Create /workspace/diff.txt with 'DIFF'\n"
                "2. List files in /workspace/\n"
                "3. Run: echo 'DONE'"
            ),
            agent_name="diff-tools-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]

        unique_tools = set(tool_names)
        assert len(unique_tools) >= 2, f"Should use at least 2 different tools. Got: {unique_tools}"

    def test_agent_completes_with_tool_calls(self, client, sandbox):
        """Agent should complete successfully with tool calls."""
        _create_agent(
            client,
            "complete-agent",
            toolkits=["sandbox_operations"],
            system_prompt="Execute the task using tools.",
        )

        events = _send_chat(
            client,
            "Create /workspace/complete.txt with content: 'COMPLETE'",
            agent_name="complete-agent",
            sandbox_id=sandbox["id"],
        )

        assert "assistant_complete" in _get_event_types(events), "Should complete successfully"

        tool_started = _get_tool_started_events(events)
        assert len(tool_started) >= 1, f"Should make at least 1 tool call. Got {len(tool_started)}"


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestFullStackWorkflow:
    """Full-stack tests that complete real workflows end-to-end."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = _create_test_sandbox("fullstack")
        yield sb
        _delete_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = _make_live_client(
            db_session_factory,
            tmp_path,
            monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with c:
            yield c

    def test_build_python_script_workflow(self, client, sandbox):
        """Build and run a Python script end-to-end."""
        _create_agent(
            client,
            "python-builder-agent",
            toolkits=["sandbox_operations"],
            system_prompt=(
                "You are a Python developer. Create and run Python scripts. "
                "First write files, then execute them with bash. "
                "Verify your work by running the scripts."
            ),
        )

        events = _send_chat(
            client,
            (
                "Complete this workflow:\n"
                "1. Create a Python script at /workspace/adder.py that defines a function add(a, b) returning a+b\n"
                "2. Create a Python script at /workspace/main.py that imports adder and prints add(3, 5)\n"
                "3. Run: python /workspace/main.py\n"
                "4. Report the output you see"
            ),
            agent_name="python-builder-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]
        tool_completed = _get_tool_completed_events(events)

        assert "daytona_write_file" in tool_names, f"Should write files. Tools: {tool_names}"
        assert "daytona_bash" in tool_names, f"Should run scripts. Tools: {tool_names}"
        assert "assistant_complete" in _get_event_types(events), "Should complete"

        text = _get_assistant_text(events)
        assert "8" in text, f"Should output 8 (3+5). Got: {text[:300]}"

    def test_multi_file_project_workflow(self, client, sandbox):
        """Create multiple files forming a mini-project."""
        _create_agent(
            client,
            "project-agent",
            toolkits=["sandbox_operations"],
            system_prompt=(
                "Create a mini project with multiple files. "
                "Write files, then verify they exist with ls."
            ),
        )

        events = _send_chat(
            client,
            (
                "Create a mini project:\n"
                '1. Create /workspace/config.json with content: {"name": "test-project", "version": "1.0.0"}\n'
                "2. Create /workspace/README.md with content: # Test Project\n"
                "3. Create /workspace/main.py with content: print('hello')\n"
                "4. List all files in /workspace/\n"
                "5. Report what files exist"
            ),
            agent_name="project-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]

        write_calls = [e for e in tool_started if e["tool_name"] == "daytona_write_file"]
        assert len(write_calls) >= 3, (
            f"Should create 3 files. Got {len(write_calls)}. Tools: {tool_names}"
        )

        assert "assistant_complete" in _get_event_types(events), "Should complete"

    def test_data_processing_workflow(self, client, sandbox):
        """Create data, process it, verify results."""
        _create_agent(
            client,
            "data-agent",
            toolkits=["sandbox_operations"],
            system_prompt=(
                "Create data files, process them, and verify results. Use bash to run commands."
            ),
        )

        events = _send_chat(
            client,
            (
                "Data processing workflow:\n"
                "1. Create /workspace/data.txt with content: line1\nline2\nline3\n"
                "2. Count lines in data.txt using: wc -l /workspace/data.txt\n"
                "3. Append 'line4' to data.txt\n"
                "4. Count lines again\n"
                "5. Report both counts"
            ),
            agent_name="data-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]

        assert "daytona_write_file" in tool_names, f"Should write file. Tools: {tool_names}"
        assert "daytona_bash" in tool_names, f"Should run commands. Tools: {tool_names}"
        assert "assistant_complete" in _get_event_types(events), "Should complete"

        text = _get_assistant_text(events).lower()
        assert "3" in text or "3 lines" in text or "line3" in text, (
            f"Should mention line count. Got: {text[:300]}"
        )

    def test_error_recovery_workflow(self, client, sandbox):
        """Handle errors and continue working."""
        _create_agent(
            client,
            "error-recovery-agent",
            toolkits=["sandbox_operations"],
            system_prompt=(
                "If a command fails, report the error and continue with the next step. "
                "Don't stop - complete all steps."
            ),
        )

        events = _send_chat(
            client,
            (
                "Complete these steps:\n"
                "1. Create /workspace/success.txt with content: 'SUCCESS'\n"
                "2. Try to read /workspace/nonexistent.txt (this will fail - report error)\n"
                "3. List /workspace/ directory\n"
                "4. Report what succeeded and what failed"
            ),
            agent_name="error-recovery-agent",
            sandbox_id=sandbox["id"],
        )

        tool_started = _get_tool_started_events(events)
        tool_names = [e["tool_name"] for e in tool_started]

        assert "daytona_write_file" in tool_names, f"Should write file. Tools: {tool_names}"
        assert "daytona_bash" in tool_names, f"Should attempt bash commands. Tools: {tool_names}"
        assert "assistant_complete" in _get_event_types(events), "Should complete even with errors"
