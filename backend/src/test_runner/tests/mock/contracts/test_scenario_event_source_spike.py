"""Phase 0 spike (HARD GATE) for the ScenarioEventSource seam.

A scripted :class:`ScenarioEventSource` drives a mock agent through the REAL
``run_ephemeral_agent`` → query loop on a docker (sweevo) sandbox — i.e. exactly
the production lifecycle, with only the event *source* swapped. This validates
the scripted event-source approach:

  1. **Tool effect through real dispatch** — a scripted ``exec_command`` call
     runs in the sandbox and its output reaches the agent.
  2. **Terminal-alone enforcement** — a terminal batched with a sibling is
     rejected by the real loop; nothing executes.
      3. **Budget parity via tool_use deltas** — the source emits one
     ``ToolUseDeltaEvent`` per tool_use, so the loop's stream-time
     ``_count_tool_dispatch`` fires identically to the real provider:
       * foreground tool counted exactly once,
       * rejected-batch tools counted (at stream time) even though they never
         execute (the §7 Symptom A fix),
       * typed command sessions use the current session tool surface.
  4. **Terminal tool_use exposed** in the returned transcript.

The api_client is built by ``spawn_agent`` but never used — ``event_source``
short-circuits the provider call — so a stub external client + a throwaway active
model row suffice (no live LLM, no API key).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from agents import AgentDefinition, AgentRole, AgentType
from engine.api import run_ephemeral_agent
from message.events import StreamEvent, ToolExecutionCompletedEvent
from runtime.app_factory import RuntimeConfig, model_store
from test_runner.agent.mock.event_source import (
    ScenarioEventSource,
    ToolCall,
    Turn,
    TurnScript,
)
from test_runner.core.stores import TaskStoreBundle
from test_runner.tests._live_config import (
    database_configured,
    rust_sandbox_runtime_unavailable_reason,
)

pytestmark = pytest.mark.asyncio
_RUST_RUNTIME_UNAVAILABLE = rust_sandbox_runtime_unavailable_reason()


class _UnusedClient:
    """``event_source`` short-circuits the loop's provider call, so this client
    is constructed by ``spawn_agent`` but never streamed from."""

    async def aclose(self) -> None:  # pragma: no cover - never used
        return None


@pytest.fixture
def _active_spike_model(stores: TaskStoreBundle) -> Iterator[None]:
    """Register + activate a throwaway model row so ``spawn_agent`` resolves a
    model id (mirrors the real_agent suite's ``_register_plan_mode_row``)."""
    prior_sf = model_store._session_factory  # noqa: SLF001 — restored on teardown
    model_store.initialize(stores.session_factory)
    key = f"test/spike-{uuid.uuid4().hex[:8]}"
    model_store.register(
        key=key,
        label="Phase-0 Event-Source Spike",
        class_path="providers.clients.anthropic_native:AnthropicClient",
        kwargs={"model": "spike-mock", "max_tokens": 4096},
        activate=True,
    )
    try:
        yield
    finally:
        try:
            model_store.delete(key)
        except Exception:
            pass
        model_store._session_factory = prior_sf  # noqa: SLF001


def _spike_agent_def(*, tools: list[str], limit: int = 16) -> AgentDefinition:
    return AgentDefinition(
        name="spike-agent",
        description="phase-0 event-source spike",
        tool_call_limit=limit,
        terminals=["submit_advisor_feedback"],
        allowed_tools=tools,
        role=AgentRole.HELPER,
        agent_type=AgentType.AGENT,
        model="inherit",
    )


async def _run_script(
    *,
    workspace: dict[str, object],
    agent_def: AgentDefinition,
    script_factory,
) -> tuple[Any, Any, list[StreamEvent]]:
    """Drive ``script_factory()`` through the real ``run_ephemeral_agent``.

    Returns ``(result, agent, events)`` — the agent is captured via
    ``on_agent_spawned`` so the test can read ``agent.query_context``.
    """
    events: list[StreamEvent] = []
    captured: dict[str, Any] = {}

    config = RuntimeConfig(
        cwd=str(workspace["repo_dir"]),
        external_api_client=_UnusedClient(),
        event_source_factory=lambda ad: ScenarioEventSource(script_factory(), agent_name=ad.name),
    )

    async def on_event(event: StreamEvent) -> None:
        events.append(event)

    def on_agent_spawned(agent: Any) -> None:
        captured["agent"] = agent

    result = await run_ephemeral_agent(
        config,
        "spike entry prompt",
        agent_def=agent_def,
        sandbox_id=str(workspace["sandbox_id"]),
        persist_agent_run=False,
        on_event=on_event,
        on_agent_spawned=on_agent_spawned,
    )
    return result, captured["agent"], events


def _tool_use_names(agent: Any) -> list[str]:
    names: list[str] = []
    for message in agent.messages:
        for block in getattr(message, "content", []):
            if getattr(block, "type", None) == "tool_use":
                names.append(block.name)
    return names


def _completions(
    events: list[StreamEvent], *, tool_name: str | None = None, is_error: bool | None = None
) -> list[ToolExecutionCompletedEvent]:
    out = []
    for event in events:
        if not isinstance(event, ToolExecutionCompletedEvent):
            continue
        if tool_name is not None and event.tool_name != tool_name:
            continue
        if is_error is not None and event.is_error != is_error:
            continue
        out.append(event)
    return out


# ---------------------------------------------------------------------------
# Criterion 1, 3 (foreground), 4: effect + foreground budget + transcript.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not database_configured(), reason="database URL not configured")
@pytest.mark.skipif(
    _RUST_RUNTIME_UNAVAILABLE is not None,
    reason=_RUST_RUNTIME_UNAVAILABLE or "Rust sandbox runtime unavailable",
)
async def test_foreground_tool_effect_and_budget_through_real_loop(
    workspace: dict[str, object],
    stores: TaskStoreBundle,
    _active_spike_model: None,
) -> None:
    async def script() -> TurnScript:
        yield Turn(
            thinking="run the marker command",
            calls=(ToolCall("exec_command", {"cmd": "echo spike-effect-ok"}),),
        )
        yield Turn(
            calls=(
                ToolCall(
                    "submit_advisor_feedback",
                    {"verdict": "approve", "summary": "spike ok"},
                ),
            )
        )

    result, agent, events = await _run_script(
        workspace=workspace,
        agent_def=_spike_agent_def(tools=["exec_command"]),
        script_factory=script,
    )

    assert result.status == "completed", result.error
    assert result.terminal_result is not None
    assert result.terminal_result.output == "spike ok"

    command_done = _completions(events, tool_name="exec_command", is_error=False)
    assert command_done, "exec_command never completed through real dispatch"
    assert "spike-effect-ok" in command_done[0].output

    # Each tool_use counted exactly once at stream time: command + terminal = 2.
    assert agent.query_context.tool_calls_used == 2

    assert "submit_advisor_feedback" in _tool_use_names(agent)


# ---------------------------------------------------------------------------
# Criterion 2 + 3 (rejected-batch): terminal-alone + stream-time counting.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not database_configured(), reason="database URL not configured")
async def test_terminal_alone_enforced_and_rejected_batch_budget(
    workspace: dict[str, object],
    stores: TaskStoreBundle,
    _active_spike_model: None,
) -> None:
    async def script() -> TurnScript:
        # Terminal batched with a sibling — the real loop must reject the whole
        # batch and execute nothing.
        yield Turn(
            calls=(
                ToolCall(
                    "submit_advisor_feedback",
                    {"verdict": "approve", "summary": "batched (should reject)"},
                ),
                ToolCall("exec_command", {"cmd": "echo should-not-run"}),
            )
        )
        # Recover with the terminal alone.
        yield Turn(
            calls=(
                ToolCall(
                    "submit_advisor_feedback",
                    {"verdict": "approve", "summary": "solo ok"},
                ),
            )
        )

    result, agent, events = await _run_script(
        workspace=workspace,
        agent_def=_spike_agent_def(tools=["exec_command"]),
        script_factory=script,
    )

    assert result.status == "completed", result.error
    assert result.terminal_result is not None
    assert result.terminal_result.output == "solo ok"

    rejected = _completions(events, is_error=True)
    assert any("must be called alone" in event.output for event in rejected), [
        e.output for e in rejected
    ]
    # The batched sibling must NOT have executed.
    assert not _completions(events, tool_name="exec_command", is_error=False)

    # Stream-time counting fires for the 2 rejected tools (even though neither
    # executed) + the solo terminal = 3 — the §7 Symptom A parity guarantee.
    assert agent.query_context.tool_calls_used == 3


# ---------------------------------------------------------------------------
# Criterion 3: typed command/session controls replace legacy background shell.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not database_configured(), reason="database URL not configured")
@pytest.mark.skipif(
    _RUST_RUNTIME_UNAVAILABLE is not None,
    reason=_RUST_RUNTIME_UNAVAILABLE or "Rust sandbox runtime unavailable",
)
async def test_command_session_control_budget_through_real_loop(
    workspace: dict[str, object],
    stores: TaskStoreBundle,
    _active_spike_model: None,
) -> None:
    async def script() -> TurnScript:
        blocks = yield Turn(
            calls=(
                ToolCall(
                    "exec_command",
                    {
                        "cmd": "bash --noprofile --norc",
                        "yield_time_ms": 0,
                    },
                ),
            )
        )
        payload = json.loads(blocks[0].content)
        command_session_id = str(payload["command_session_id"])
        yield Turn(
            calls=(
                ToolCall(
                    "write_stdin",
                    {
                        "command_session_id": command_session_id,
                        "chars": "echo command-session-done; exit\n",
                    },
                ),
            )
        )
        yield Turn(
            calls=(
                ToolCall(
                    "submit_advisor_feedback",
                    {"verdict": "approve", "summary": "command session ok"},
                ),
            )
        )

    result, agent, events = await _run_script(
        workspace=workspace,
        agent_def=_spike_agent_def(tools=["exec_command", "write_stdin"]),
        script_factory=script,
    )

    assert result.status == "completed", result.error
    assert result.terminal_result is not None
    assert result.terminal_result.output == "command session ok"

    # Command-session body actually executed in the sandbox.
    assert any(
        "command-session-done" in event.output
        for event in _completions(events, is_error=False)
    ), [
        e.output for e in _completions(events)
    ]

    # Accounting: exec_command + write_stdin + terminal.
    assert agent.query_context.tool_calls_used == 3
