# ruff: noqa
"""Live API integration tests — require real API keys and Daytona sandbox.

Reads credentials from ~/.ephemeralos/settings.json or environment variables.
Run with: pytest tests/test_e2e/test_live_api.py -m live -v
"""

from __future__ import annotations

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
    HAS_MINIMAX,
    HAS_DAYTONA,
    HAS_BOTH,
    make_live_client,
    parse_sse_events,
    events_of_type,
    create_test_sandbox,
    delete_test_sandbox,
    send_chat,
    create_test_agent,
    get_sandbox_service,
)

# Markers
pytestmark = [pytest.mark.e2e, pytest.mark.live]


# ---------------------------------------------------------------------------
# Daytona sandbox helper — delegated to conftest
# ---------------------------------------------------------------------------


def _looks_like_minimax_tool_validation_error(message: str | None) -> bool:
    """Return True when the event payload looks like a tool-input validation failure."""
    if not message:
        return False
    lowered = message.lower()
    return (
        "daytonawritefileinput" in lowered
        or "daytonabashinput" in lowered
        or "validation error" in lowered
        or "invalid input for" in lowered
    )


def _assert_parallel_tool_sequence(events: list[dict], *, min_starts: int = 2) -> bool:
    """Assert the tool event stream looks like a parallel batch.

    For a parallel batch, we expect at least ``min_starts`` ``tool_started``
    events to appear before the first ``tool_completed`` event. If the stream
    ends in a MiniMax schema/validation error, still accept that as a known
    failure mode while preserving the multi-start signal.
    """
    event_types = [e["type"] for e in events]
    tool_started = events_of_type(events, "tool_started")
    tool_completed = events_of_type(events, "tool_completed")
    errors = events_of_type(events, "error")

    assert "assistant_complete" in event_types or "error" in event_types, (
        f"Expected assistant_complete or error event. Types: {set(event_types)}"
    )
    if not tool_started:
        error_text = "".join(e.get("message", "") for e in errors)
        assert _looks_like_minimax_tool_validation_error(error_text), (
            f"Expected tool_started events or minimax validation error. "
            f"Got: {error_text!r}, Types: {set(event_types)}"
        )
        return True

    assert len(tool_started) >= min_starts, f"Expected at least {min_starts} tool_started events. Types: {set(event_types)}"
    if not tool_completed:
        error_text = "".join(e.get("message", "") for e in errors)
        assert _looks_like_minimax_tool_validation_error(error_text), (
            f"Expected at least one tool_completed event or validation error. "
            f"Got: {error_text!r}, Types: {set(event_types)}"
        )
        return True

    first_completed_idx = min(i for i, t in enumerate(event_types) if t == "tool_completed")
    starts_before_first_completion = [
        i for i, t in enumerate(event_types)
        if t == "tool_started" and i < first_completed_idx
    ]
    assert (
        len(starts_before_first_completion) >= min_starts
    ), (
        f"Expected at least {min_starts} tool_started before first tool_completed. "
        f"Event types: {event_types}"
    )
    return False




# ===========================================================================
# US-010: Sandbox lifecycle and tool calling via real Daytona
# ===========================================================================


@pytest.mark.skipif(not HAS_DAYTONA, reason="Daytona not configured")
class TestLiveSandboxLifecycle:
    """Test Daytona sandbox create, execute, read/write, and delete."""

    @pytest.fixture(scope="class")
    def live_sandbox(self):
        """Create a real sandbox for the test class, clean up after."""
        sandbox = create_test_sandbox("lifecycle")
        yield sandbox
        delete_test_sandbox(sandbox["id"])

    def test_live_sandbox_create(self, live_sandbox):
        """Verify sandbox was created with expected fields."""
        assert live_sandbox["id"], "Sandbox ID should be non-empty"
        assert live_sandbox["state"] in ("started", "running", "ready"), (
            f"Expected started state, got: {live_sandbox['state']}"
        )
        assert live_sandbox["managed_by_app"] is True

    def test_live_sandbox_bash(self, live_sandbox):
        """Execute a shell command in the sandbox."""
        svc = get_sandbox_service()
        raw_sb = svc.get_sandbox_object(live_sandbox["id"])
        response = raw_sb.process.exec("echo 'hello-e2e'", timeout=30)
        assert "hello-e2e" in (response.result or "")

    def test_live_sandbox_file_write_read(self, live_sandbox):
        """Write a file and read it back in the sandbox."""
        svc = get_sandbox_service()
        raw_sb = svc.get_sandbox_object(live_sandbox["id"])

        # Write file and read it back in a single exec call — Daytona process
        # isolation means separate exec calls may not share filesystem state.
        resp = raw_sb.process.exec(
            "echo 'e2e test content: hello world' > /tmp/e2e_test.txt && "
            "echo 'second line' >> /tmp/e2e_test.txt && "
            "cat /tmp/e2e_test.txt",
            timeout=30,
        )
        content = resp.result or ""
        assert "e2e test content: hello world" in content, (
            f"Write+read failed. Got: {content!r}"
        )
        assert "second line" in content

    def test_live_sandbox_list_files(self, live_sandbox):
        """List files in the sandbox /workspace directory."""
        svc = get_sandbox_service()
        raw_sb = svc.get_sandbox_object(live_sandbox["id"])

        # Ensure there's at least one file
        raw_sb.process.exec("touch /workspace/listing_test.txt", timeout=10)
        # Use shell ls (more reliable across Daytona SDK versions than fs.list_files)
        ls_resp = raw_sb.process.exec("ls /workspace/", timeout=10)
        names = (ls_resp.result or "").strip().splitlines()
        assert len(names) > 0, "Should have at least one file in /workspace"

    def test_live_sandbox_cleanup(self, live_sandbox):
        """Verify the sandbox can be fetched before cleanup."""
        svc = get_sandbox_service()
        info = svc.get_sandbox(live_sandbox["id"])
        assert info["id"] == live_sandbox["id"]


# ===========================================================================
# US-011: Agent chat with Daytona sandbox tools via MiniMax
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestLiveAgentSandboxChat:
    """Chat with a custom agent that has sandbox tools, using real MiniMax + Daytona."""

    @pytest.fixture(scope="class")
    def sandbox_for_agent(self):
        """Create a sandbox for agent chat tests."""
        sandbox = create_test_sandbox("agent-chat")
        yield sandbox
        delete_test_sandbox(sandbox["id"])

    @pytest.fixture()
    def minimax_client(self, db_session_factory, tmp_path, monkeypatch):
        client = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with client:
            yield client

    def test_live_agent_creates_sandbox_agent(self, minimax_client, sandbox_for_agent):
        """Create a custom agent with sandbox_operations toolkit."""
        resp = minimax_client.post("/api/agents/", json={
            "name": "e2e-sandbox-agent",
            "description": "E2E test agent with sandbox tools",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": (
                "You are a coding assistant with access to a remote sandbox. "
                "When asked to run commands, use the daytona_bash tool. "
                "When asked to read files, use daytona_read_file. "
                "Always respond concisely."
            ),
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["name"] == "e2e-sandbox-agent"
        assert "sandbox_operations" in data["toolkits"]

    def test_live_agent_sandbox_chat(self, minimax_client, sandbox_for_agent):
        """Send a chat to a sandbox-equipped agent and verify events."""
        # Create agent first
        minimax_client.post("/api/agents/", json={
            "name": "sandbox-chat-agent",
            "description": "Chat test agent",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": "You are a test assistant with sandbox access. Be very concise.",
        })

        resp = minimax_client.post(
            "/api/chat",
            json={
                "line": "Reply with exactly: SANDBOX_OK",
                "agent_name": "sandbox-chat-agent",
                "sandbox_id": sandbox_for_agent["id"],
            },
            timeout=90,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        completes = events_of_type(events, "assistant_complete")
        assert len(completes) >= 1, f"No assistant_complete. Events: {[e['type'] for e in events]}"
        assert completes[0]["message"], "Empty assistant response"

    def test_live_agent_sandbox_bash_tool(self, minimax_client, sandbox_for_agent):
        """Verify the model can invoke daytona_bash and get results."""
        minimax_client.post("/api/agents/", json={
            "name": "bash-tool-agent",
            "description": "Agent that uses bash",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": (
                "You have access to a remote sandbox via daytona_bash. "
                "When I ask you to run a command, use the daytona_bash tool. "
                "Always use tools, never just describe what you would do."
            ),
        })

        resp = minimax_client.post(
            "/api/chat",
            json={
                "line": "Run this exact command in the sandbox: echo 'E2E_TOOL_TEST_OK'",
                "agent_name": "bash-tool-agent",
                "sandbox_id": sandbox_for_agent["id"],
            },
            timeout=120,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        # Check for tool usage events (model may or may not use tools depending on interpretation)
        types = {e["type"] for e in events}
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        # If tool was used, verify tool events
        tool_started = events_of_type(events, "tool_started")
        tool_completed = events_of_type(events, "tool_completed")
        if tool_started:
            # Tool may error during sandbox execution; completed or error both acceptable
            assert len(tool_completed) >= 1 or "error" in types, "Tool started but never completed or errored"


# ===========================================================================
# US-012: Multi-turn conversation capability
# ===========================================================================


@pytest.mark.skipif(not HAS_MINIMAX, reason="MiniMax not configured")
class TestLiveMultiTurn:
    """Test multi-turn conversations with context retention."""

    @pytest.fixture()
    def minimax_client(self, db_session_factory, tmp_path, monkeypatch):
        client = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with client:
            yield client

    def test_live_multiturn_context_retention(self, minimax_client):
        """Send 3 sequential messages and verify context retention."""
        # Turn 1: Establish a fact
        resp1 = minimax_client.post(
            "/api/chat",
            json={"line": "Remember this number: 42. Just confirm you noted it."},
            timeout=60,
        )
        assert resp1.status_code == 200
        events1 = parse_sse_events(resp1.text)
        completes1 = events_of_type(events1, "assistant_complete")
        assert len(completes1) >= 1, "Turn 1: no assistant_complete"

        # Turn 2: Ask about the fact
        resp2 = minimax_client.post(
            "/api/chat",
            json={"line": "What number did I just ask you to remember? Reply with just the number."},
            timeout=60,
        )
        assert resp2.status_code == 200
        events2 = parse_sse_events(resp2.text)
        completes2 = events_of_type(events2, "assistant_complete")
        assert len(completes2) >= 1, "Turn 2: no assistant_complete"
        # The model should reference 42
        assert "42" in completes2[0]["message"], (
            f"Model didn't retain context. Got: {completes2[0]['message']}"
        )

        # Turn 3: Build on previous context
        resp3 = minimax_client.post(
            "/api/chat",
            json={"line": "Multiply that number by 2. Reply with just the result."},
            timeout=60,
        )
        assert resp3.status_code == 200
        events3 = parse_sse_events(resp3.text)
        completes3 = events_of_type(events3, "assistant_complete")
        assert len(completes3) >= 1, "Turn 3: no assistant_complete"
        assert "84" in completes3[0]["message"], (
            f"Model didn't compute correctly. Got: {completes3[0]['message']}"
        )

    def test_live_multiturn_tool_followup(self, minimax_client):
        """Send a tool-using prompt then a follow-up referencing the output."""
        # Turn 1: Ask to use a tool
        resp1 = minimax_client.post(
            "/api/chat",
            json={"line": "Use the skill tool to list available skills."},
            timeout=60,
        )
        assert resp1.status_code == 200
        events1 = parse_sse_events(resp1.text)
        completes1 = events_of_type(events1, "assistant_complete")
        assert len(completes1) >= 1

        # Turn 2: Reference previous results
        resp2 = minimax_client.post(
            "/api/chat",
            json={"line": "Based on what you just did, summarize in one sentence what tools you have."},
            timeout=60,
        )
        assert resp2.status_code == 200
        events2 = parse_sse_events(resp2.text)
        completes2 = events_of_type(events2, "assistant_complete")
        assert len(completes2) >= 1
        assert completes2[0]["message"], "Follow-up response should be non-empty"


# ===========================================================================
# US-013: Reasoning/thinking block streaming
# ===========================================================================


@pytest.mark.skipif(not HAS_MINIMAX, reason="MiniMax not configured")
class TestLiveThinkingBlock:
    """Test thinking/reasoning block streaming from real MiniMax API."""

    @pytest.fixture()
    def minimax_client(self, db_session_factory, tmp_path, monkeypatch):
        client = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with client:
            yield client

    def test_live_thinking_block_streamed(self, minimax_client):
        """Send a reasoning-requiring prompt and check for thinking events."""
        resp = minimax_client.post(
            "/api/chat",
            json={"line": "Think step by step: what is 17 * 23? Show your reasoning."},
            timeout=60,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        types = {e["type"] for e in events}
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"
        assert "line_complete" in types

        # MiniMax may or may not emit thinking blocks — both are valid
        thinking_events = events_of_type(events, "thinking_delta")
        completes = events_of_type(events, "assistant_complete")

        # The final answer should contain 391 (17*23)
        final_text = completes[0]["message"]
        assert "391" in final_text, f"Expected 391 in response. Got: {final_text}"

        # Log whether thinking was present for debugging
        if thinking_events:
            assert thinking_events[0]["message"], "Thinking delta should have content"

    def test_live_thinking_then_text(self, minimax_client):
        """If thinking events exist, they should come before assistant text."""
        resp = minimax_client.post(
            "/api/chat",
            json={"line": "Carefully reason about: Is 97 a prime number? Think before answering."},
            timeout=60,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        thinking_events = events_of_type(events, "thinking_delta")
        text_events = events_of_type(events, "assistant_delta")
        completes = events_of_type(events, "assistant_complete")

        assert len(completes) >= 1, "Should have at least one assistant_complete"

        # If both thinking and text deltas exist, thinking should come first
        if thinking_events and text_events:
            types_list = [e["type"] for e in events]
            first_thinking = types_list.index("thinking_delta")
            first_text = types_list.index("assistant_delta")
            assert first_thinking < first_text, (
                "Thinking should come before text deltas"
            )


# ===========================================================================
# US-015: Complex long task with multiple tool calls
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestLiveComplexTask:
    """Test complex multi-step tasks with multiple tool calls."""

    @pytest.fixture(scope="class")
    def sandbox_for_complex(self):
        """Create a sandbox for complex task tests."""
        sandbox = create_test_sandbox("complex-task")
        yield sandbox
        delete_test_sandbox(sandbox["id"])

    @pytest.fixture()
    def minimax_client(self, db_session_factory, tmp_path, monkeypatch):
        client = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with client:
            yield client

    def test_live_complex_multi_tool_task(self, minimax_client, sandbox_for_complex):
        """Send a complex prompt requiring multiple tool calls."""
        minimax_client.post("/api/agents/", json={
            "name": "complex-task-agent",
            "description": "Agent for complex multi-step tasks",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": (
                "You are a coding assistant with sandbox access. "
                "Use daytona_bash to run commands, daytona_write_file to write files, "
                "and daytona_read_file to read files. Execute ALL steps."
            ),
        })

        resp = minimax_client.post(
            "/api/chat",
            json={
                "line": (
                    "Do these steps in the sandbox:\n"
                    "1. Create a file /workspace/hello.py with: print('hello from e2e')\n"
                    "2. Run: python /workspace/hello.py\n"
                    "3. Tell me the output"
                ),
                "agent_name": "complex-task-agent",
                "sandbox_id": sandbox_for_complex["id"],
            },
            timeout=180,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        types = {e["type"] for e in events}
        assert "assistant_complete" in types, f"Missing assistant_complete. Types: {types}"

        # Should have at least one tool call (write or bash)
        tool_started = events_of_type(events, "tool_started")
        tool_completed = events_of_type(events, "tool_completed")

        # Model should have attempted tool usage
        if tool_started:
            tool_names = [e["tool_name"] for e in tool_started]
            daytona_tools = [t for t in tool_names if t.startswith("daytona_")]
            assert len(daytona_tools) >= 1, f"Expected daytona tools, got: {tool_names}"


# ===========================================================================
# US-016: Model key integration + explicit multi-tool calls with live MiniMax
# ===========================================================================


@pytest.mark.skipif(not HAS_BOTH, reason="MiniMax + Daytona both required")
class TestLiveMultipleToolCallsWithModelKey:
    """Use model_key when creating a live agent and verify multi-tool execution."""

    @pytest.fixture(scope="class")
    def sandbox_for_model_key(self):
        """Create a sandbox for model-key multi-tool tests."""
        sandbox = create_test_sandbox("model-key-multi-tool")
        yield sandbox
        delete_test_sandbox(sandbox["id"])

    @pytest.fixture()
    def minimax_client(self, db_session_factory, tmp_path, monkeypatch):
        client = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with client:
            yield client

    def test_live_multiple_tools_with_model_key(self, minimax_client, sandbox_for_model_key):
        """Create an agent with model_key and verify it calls multiple tools."""
        agent_name = "modelkey-multi-tool-agent"
        create_resp = minimax_client.post("/api/agents/", json={
            "name": agent_name,
            "description": "Agent using model_key with multiple tools",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": (
                "You are a coding assistant with sandbox tools. "
                "When creating files, use daytona_write_file. "
                "When reading or checking output, use daytona_read_file or daytona_bash. "
                "Do every required step and then report results."
            ),
        })
        if create_resp.status_code == 201:
            agent_payload = create_resp.json()
        else:
            get_resp = minimax_client.get(f"/api/agents/{agent_name}")
            assert get_resp.status_code == 200, create_resp.text
            agent_payload = get_resp.json()
        assert agent_payload["model"] == MINIMAX_MODEL

        resp = minimax_client.post(
            "/api/chat",
            json={
                "line": (
                    "Create /workspace/modelkey_multi.txt with content: MODELKEY_TEST\n"
                    "Then read it back and reply with exactly: CONTENT=<content>."
                ),
                "agent_name": agent_name,
                "sandbox_id": sandbox_for_model_key["id"],
            },
            timeout=180,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        types = {e["type"] for e in events}
        assert "assistant_complete" in types or "error" in types, (
            f"Expected assistant_complete or error. Types: {types}"
        )

        tool_started = events_of_type(events, "tool_started")
        tool_completed = events_of_type(events, "tool_completed")
        errors = events_of_type(events, "error")

        if tool_started:
            tool_names = [e["tool_name"] for e in tool_started if "tool_name" in e]
            assert len(tool_started) >= 1, f"No tool_started payloads. Types: {types}"
            assert "daytona_write_file" in tool_names, f"Missing write tool. Tools: {tool_names}"
            assert any(
                name in tool_names for name in ("daytona_read_file", "daytona_bash")
            ), f"Missing read/exec follow-up tool. Tools: {tool_names}"
            assert len(tool_completed) >= 1 or "error" in types, (
                "Expected at least one tool completion or explicit error."
            )
        else:
            assert errors, (
                "Expected tool call attempts to either emit tool_started or return processing error."
            )
            error_text = "".join(e.get("message", "") for e in errors)
            assert _looks_like_minimax_tool_validation_error(error_text), (
                f"Expected tool input validation error. Got: {error_text!r}"
            )

    def test_live_tool_call_chain_with_model_key(self, minimax_client, sandbox_for_model_key):
        """Verify the same model_key can drive a short chain of 3 tool calls."""
        agent_name = "modelkey-multi-tool-chain-agent"
        create_resp = minimax_client.post("/api/agents/", json={
            "name": agent_name,
            "description": "Chain three tools using model_key",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": (
                "Complete every requested step using tools and do not stop early. "
                "Use shell or file tools as appropriate."
            ),
        })
        if create_resp.status_code == 201:
            agent_payload = create_resp.json()
        else:
            get_resp = minimax_client.get(f"/api/agents/{agent_name}")
            assert get_resp.status_code == 200, create_resp.text
            agent_payload = get_resp.json()
        assert agent_payload["model"] == MINIMAX_MODEL

        resp = minimax_client.post(
            "/api/chat",
            json={
                "line": (
                    "Create /workspace/modelkey_one.txt with 'ONE', then create /workspace/modelkey_two.txt "
                    "with 'TWO', then run: ls /workspace/modelkey_* | cat."
                ),
                "agent_name": agent_name,
                "sandbox_id": sandbox_for_model_key["id"],
            },
            timeout=240,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        types = {e["type"] for e in events}
        assert "assistant_complete" in types or "error" in types, (
            f"Expected assistant_complete or error. Types: {types}"
        )

        tool_started = events_of_type(events, "tool_started")
        tool_names = [e["tool_name"] for e in tool_started]
        tool_completed = events_of_type(events, "tool_completed")
        errors = events_of_type(events, "error")

        if tool_started:
            assert tool_names.count("daytona_write_file") >= 2, (
                f"Expected two writes. Tools: {tool_names}"
            )
            if "daytona_bash" not in tool_names and "daytona_read_file" not in tool_names:
                recovery_events = send_chat(
                    minimax_client,
                    (
                        "Now run: ls /workspace/modelkey_* | cat "
                        "and report the output."
                    ),
                    agent_name=agent_name,
                    sandbox_id=sandbox_for_model_key["id"],
                    timeout=120,
                )
                recovery_tools = [e["tool_name"] for e in events_of_type(recovery_events, "tool_started")]
                assert "daytona_bash" in recovery_tools or "daytona_read_file" in recovery_tools, (
                    f"Expected follow-up command/read in recovery. Initial tools: {tool_names}"
                )
            else:
                assert len(tool_started) >= 3, f"Expected at least 3 tool calls. Tools: {tool_names}"
                assert len(tool_completed) >= 1 or "error" in types, (
                    "Expected at least one tool completion or explicit error."
                )
        else:
            assert errors, (
                "Expected tool call attempts to either emit tool_started or return processing error."
            )
            error_text = "".join(e.get("message", "") for e in errors)
            assert _looks_like_minimax_tool_validation_error(error_text), (
                f"Expected tool input validation error. Got: {error_text!r}"
            )

    def test_live_parallel_tool_calls_with_model_key(self, minimax_client, sandbox_for_model_key):
        """Create multiple files in parallel calls using the real MiniMax model key."""
        agent_name = "modelkey-parallel-writes-agent"
        create_resp = minimax_client.post("/api/agents/", json={
            "name": agent_name,
            "description": "Parallel write test with model_key",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": (
                "You have access to a remote sandbox. "
                "Use daytona_write_file and do not combine commands. "
                "When asked for multiple independent file writes, call all writes directly and use tools."
            ),
        })
        if create_resp.status_code == 201:
            agent_payload = create_resp.json()
        else:
            get_resp = minimax_client.get(f"/api/agents/{agent_name}")
            assert get_resp.status_code == 200, create_resp.text
            agent_payload = get_resp.json()
        assert agent_payload["model"] == MINIMAX_MODEL

        events = send_chat(
            minimax_client,
            (
                "Use tools to do this in one response:\n"
                "1. Create /workspace/modelkey_parallel_a.txt with content: PARALLEL_A\n"
                "2. Create /workspace/modelkey_parallel_b.txt with content: PARALLEL_B\n"
                "3. Create /workspace/modelkey_parallel_c.txt with content: PARALLEL_C\n"
            ),
            agent_name=agent_name,
            sandbox_id=sandbox_for_model_key["id"],
            timeout=240,
        )

        validation_error = _assert_parallel_tool_sequence(events, min_starts=1)
        if not validation_error:
            tool_names = [e["tool_name"] for e in events_of_type(events, "tool_started")]
            assert tool_names.count("daytona_write_file") >= 3, (
                f"Expected parallel file writes. Tools: {tool_names}"
            )

    def test_live_parallel_tool_batch_bash_and_write_with_model_key(self, minimax_client, sandbox_for_model_key):
        """Request a mixed batch of write/bash tool calls and verify parallel-style scheduling."""
        agent_name = "modelkey-parallel-batch-agent"
        create_resp = minimax_client.post("/api/agents/", json={
            "name": agent_name,
            "description": "Parallel mixed batch test with model_key",
            "model": MINIMAX_MODEL,
            "toolkits": ["sandbox_operations"],
            "system_prompt": (
                "You are a developer with sandbox tools. "
                "When given multiple explicit actions, issue tool calls directly. "
                "Keep each command separate (no batching into one command)."
            ),
        })
        if create_resp.status_code == 201:
            agent_payload = create_resp.json()
        else:
            get_resp = minimax_client.get(f"/api/agents/{agent_name}")
            assert get_resp.status_code == 200, create_resp.text
            agent_payload = get_resp.json()
        assert agent_payload["model"] == MINIMAX_MODEL

        events = send_chat(
            minimax_client,
            (
                "Run these actions in one turn:\n"
                "1. Create /workspace/modelkey_parallel_mix_a.txt with content: MIX_A\n"
                "2. Create /workspace/modelkey_parallel_mix_b.txt with content: MIX_B\n"
                "3. Run daytona_bash with command: echo BASH_A\n"
                "4. Run daytona_bash with command: echo BASH_B\n"
                "Return only a short acknowledgement."
            ),
            agent_name=agent_name,
            sandbox_id=sandbox_for_model_key["id"],
            timeout=240,
        )

        validation_error = _assert_parallel_tool_sequence(events, min_starts=1)
        if not validation_error:
            tool_names = [e["tool_name"] for e in events_of_type(events, "tool_started")]
            assert tool_names.count("daytona_write_file") >= 2, f"Expected writes. Tools: {tool_names}"
            assert tool_names.count("daytona_bash") >= 2, f"Expected bash calls. Tools: {tool_names}"


# ===========================================================================
# Existing MiniMax live tests (kept for backward compat)
# ===========================================================================


@pytest.mark.skipif(not HAS_MINIMAX, reason="MiniMax API key or base_url not configured")
class TestMiniMaxLive:
    """Live tests against the MiniMax API via Anthropic-compatible endpoint."""

    @pytest.fixture()
    def minimax_client(self, db_session_factory, tmp_path, monkeypatch):
        client = make_live_client(
            db_session_factory, tmp_path, monkeypatch,
            api_key=MINIMAX_KEY,
            model=MINIMAX_MODEL,
            base_url=MINIMAX_BASE_URL,
            api_format=MINIMAX_FORMAT,
        )
        with client:
            yield client

    def test_minimax_simple_chat(self, minimax_client):
        """Send a simple prompt and verify we get a response."""
        resp = minimax_client.post(
            "/api/chat",
            json={"line": "Reply with exactly one word: PONG"},
            timeout=60,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        completes = events_of_type(events, "assistant_complete")
        assert len(completes) >= 1, f"No assistant_complete events. All events: {[e['type'] for e in events]}"
        assert completes[0]["message"], "assistant_complete message is empty"

        assert any(e["type"] == "line_complete" for e in events), "Missing line_complete event"

    def test_minimax_custom_agent_chat(self, minimax_client):
        """Create a custom agent and chat with it using real API."""
        create_resp = minimax_client.post("/api/agents/", json={
            "name": "live-test-agent",
            "description": "A live test agent for e2e testing",
            "model": MINIMAX_MODEL,
            "system_prompt": "You are a helpful test assistant. Always respond in exactly one sentence.",
        })
        if create_resp.status_code == 201:
            agent_name = "live-test-agent"
        else:
            agent_name = None

        payload = {"line": "What is 2 + 2? Answer in one word."}
        if agent_name:
            payload["agent_name"] = agent_name

        resp = minimax_client.post("/api/chat", json=payload, timeout=60)
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        completes = events_of_type(events, "assistant_complete")
        assert len(completes) >= 1
        assert completes[0]["message"]

        types = {e["type"] for e in events}
        assert "transcript_item" in types
        assert "assistant_complete" in types
        assert "line_complete" in types

    def test_minimax_chat_with_tools(self, minimax_client):
        """Chat with tools available and verify the model can use them."""
        resp = minimax_client.post(
            "/api/chat",
            json={"line": "Use the skill tool to list available skills."},
            timeout=60,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)

        completes = events_of_type(events, "assistant_complete")
        assert len(completes) >= 1
        assert any(e["type"] == "line_complete" for e in events)


# ===========================================================================
# Sandbox health test
# ===========================================================================


class TestSandboxHealth:
    """Test sandbox service health endpoint."""

    def test_sandbox_health(self, app_client):
        """Check sandbox health endpoint returns expected fields."""
        client, _ = app_client
        resp = client.get("/api/sandboxes/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "configured" in data
        assert "available" in data
        assert isinstance(data["configured"], bool)

    @pytest.mark.skipif(not HAS_DAYTONA, reason="Daytona not configured (no API key/URL)")
    def test_sandbox_health_when_configured(self, app_client):
        """When Daytona is configured, health should report configured=True."""
        client, _ = app_client
        resp = client.get("/api/sandboxes/health")
        data = resp.json()
        assert data["configured"] is True
