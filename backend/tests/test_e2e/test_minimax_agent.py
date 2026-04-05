# ruff: noqa
"""Live E2E: test-minimax-agent — real MiniMax LLM + real Daytona sandbox.

This agent is equipped with the MiniMax model and uses Daytona sandbox
for all tool execution. Tests the complete pipeline:
- Agent creation with MiniMax model
- Sandbox attachment
- Tool invocation (bash, read, write, glob, grep)
- Result verification

Run with: pytest tests/test_e2e/test_minimax_agent.py -m live -v
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_e2e.conftest import (
    HAS_BOTH,
    MINIMAX_MODEL,
    create_test_agent,
    create_test_sandbox,
    delete_test_sandbox,
    events_of_type,
    get_assistant_text,
    get_event_types,
    get_tool_started_events,
    make_live_client,
    send_chat,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live]


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------

MINIMAX_AGENT_NAME = "test-minimax-agent"
MINIMAX_AGENT_TOOLKITS = ["sandbox_operations"]
MINIMAX_AGENT_PROMPT = (
    "You are test-minimax-agent, a developer with a remote Daytona sandbox. "
    "You MUST use tools for every action — never just describe what you'd do. "
    "Use daytona_write_file to create files, daytona_bash to run commands, "
    "daytona_read_file to read files, daytona_list_files to list directories, "
    "daytona_grep to search content, daytona_glob to find files. "
    "Always execute every step using tools. Be concise."
)


# ===========================================================================
# Test Cases
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestMiniMaxAgentBasic:
    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("minimax-basic")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_agent_created_with_minimax_model(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        resp = client.get(f"/api/agents/{MINIMAX_AGENT_NAME}")
        assert resp.status_code == 200
        agent = resp.json()
        assert agent["name"] == MINIMAX_AGENT_NAME
        assert agent["model"] == MINIMAX_MODEL

    def test_agent_responds_to_simple_prompt(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        events = send_chat(
            client,
            "Say hello in exactly 3 words.",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=60,
        )
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        text = get_assistant_text(events)
        assert text, "Should produce a response"

    def test_agent_uses_daytona_bash_tool(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        events = send_chat(
            client,
            "Run this exact command in the sandbox: echo 'MINIMAX_BASH_OK'",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        types = get_event_types(events)
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        tool_started = events_of_type(events, "tool_started")
        tool_names = [e.get("tool_name") for e in tool_started]
        assert any("daytona" in t for t in tool_names), f"No daytona tool used: {tool_names}"

    def test_agent_write_and_read_file(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        marker = f"MINIMAX_READBACK_{uuid.uuid4().hex[:8]}"
        events = send_chat(
            client,
            f"Write '{marker}' to /workspace/minimax_test.txt using daytona_write_file, "
            f"then read it back using daytona_bash: cat /workspace/minimax_test.txt",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        types = get_event_types(events)
        assert "assistant_complete" in types

        tool_completed = events_of_type(events, "tool_completed")
        all_output = " ".join(e.get("output", "") for e in tool_completed)
        text = get_assistant_text(events)

        has_marker = marker in all_output or marker in text
        assert has_marker, (
            f"Should find marker '{marker}' in output. Output: {all_output[:200]}, Text: {text[:200]}"
        )

    def test_agent_lists_files(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        events = send_chat(
            client,
            "Use daytona_list_files to list the /workspace directory",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        types = get_event_types(events)
        assert "assistant_complete" in types

        tool_started = events_of_type(events, "tool_started")
        tool_names = [e.get("tool_name") for e in tool_started]
        assert any("daytona_list_files" in t or "daytona_bash" in t for t in tool_names), (
            f"Expected list_files or bash tool. Got: {tool_names}"
        )

    def test_agent_grep_search(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        send_chat(
            client,
            "Use daytona_bash to run: echo 'GREP_TARGET_MINIMAX' > /workspace/searchable.txt",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        events = send_chat(
            client,
            "Use daytona_grep to search for 'GREP_TARGET' in /workspace/",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        types = get_event_types(events)
        assert "assistant_complete" in types

        tool_started = events_of_type(events, "tool_started")
        tool_names = [e.get("tool_name") for e in tool_started]
        assert any("daytona_grep" in t or "daytona_bash" in t for t in tool_names), (
            f"Expected grep or bash tool. Got: {tool_names}"
        )

    def test_agent_glob_find(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        send_chat(
            client,
            "Use daytona_bash to run: touch /workspace/glob_test_1.txt /workspace/glob_test_2.txt",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        events = send_chat(
            client,
            "Use daytona_glob to find all .txt files in /workspace/",
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        types = get_event_types(events)
        assert "assistant_complete" in types

    def test_agent_multi_step_pipeline(self, client, sandbox):
        create_test_agent(
            client,
            MINIMAX_AGENT_NAME,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        events = send_chat(
            client,
            (
                "Do these steps in the sandbox:\n"
                "1. Use daytona_write_file to create /workspace/pipeline.py with: print('PIPELINE_OK')\n"
                "2. Use daytona_bash to run: python3 /workspace/pipeline.py\n"
                "3. Report the output"
            ),
            agent_name=MINIMAX_AGENT_NAME,
            sandbox_id=sandbox["id"],
            timeout=180,
        )
        types = get_event_types(events)
        assert "assistant_complete" in types

        tool_started = events_of_type(events, "tool_started")
        if tool_started:
            daytona_tools = [
                t
                for t in tool_started
                if isinstance(t, dict) and "daytona" in t.get("tool_name", "")
            ]
            assert len(daytona_tools) >= 1, (
                f"Expected daytona tools. Got: {[t.get('tool_name') for t in tool_started]}"
            )

        tool_completed = events_of_type(events, "tool_completed")
        all_output = " ".join(e.get("output", "") for e in tool_completed)
        text = get_assistant_text(events)

        has_pipeline = "PIPELINE_OK" in all_output or "PIPELINE_OK" in text
        assert has_pipeline or len(tool_started) >= 2, (
            f"Should execute pipeline or use multiple tools. "
            f"Output: {all_output[:200]}, Text: {text[:200]}"
        )


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestMiniMaxAgentEventStructure:
    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("minimax-events")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.fixture()
    def client(self, db_session_factory, tmp_path, monkeypatch):
        c = make_live_client(db_session_factory, tmp_path, monkeypatch)
        with c:
            yield c

    def test_tool_started_has_correct_structure(self, client, sandbox):
        agent_name = f"{MINIMAX_AGENT_NAME}-events"
        create_test_agent(
            client,
            agent_name,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        events = send_chat(
            client,
            "Use daytona_bash to run: echo 'STRUCTURE_OK'",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        tool_started = events_of_type(events, "tool_started")
        assert len(tool_started) >= 1, f"No tool_started events. Types: {get_event_types(events)}"

        for ev in tool_started:
            tool_input = ev.get("tool_input")
            assert tool_input is not None, f"tool_started missing tool_input: {ev}"
            assert isinstance(tool_input, dict), f"tool_input should be dict: {type(tool_input)}"

            name = ev.get("tool_name", "")
            if name == "daytona_bash":
                assert "command" in tool_input, f"daytona_bash missing 'command': {tool_input}"

    def test_tool_completed_has_output(self, client, sandbox):
        agent_name = f"{MINIMAX_AGENT_NAME}-events2"
        create_test_agent(
            client,
            agent_name,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        events = send_chat(
            client,
            "Use daytona_bash to run: echo 'OUTPUT_CHECK'",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        tool_completed = events_of_type(events, "tool_completed")
        if tool_completed:
            for ev in tool_completed:
                if not ev.get("is_error", False):
                    output = ev.get("output", "")
                    assert output, f"Successful tool_completed has empty output: {ev}"

    def test_event_lifecycle_complete(self, client, sandbox):
        agent_name = f"{MINIMAX_AGENT_NAME}-events3"
        create_test_agent(
            client,
            agent_name,
            toolkits=MINIMAX_AGENT_TOOLKITS,
            system_prompt=MINIMAX_AGENT_PROMPT,
        )
        events = send_chat(
            client,
            "Use daytona_bash to run: echo 'LIFECYCLE_OK'",
            agent_name=agent_name,
            sandbox_id=sandbox["id"],
            timeout=120,
        )
        types = get_event_types(events)

        assert "transcript_item" in types, f"Missing transcript_item. Types: {types}"
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"
        if "tool_started" in types and "error" not in types:
            assert "tool_completed" in types, f"tool_started without tool_completed. Types: {types}"
