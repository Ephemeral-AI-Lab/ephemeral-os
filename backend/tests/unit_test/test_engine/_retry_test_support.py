"""Shared scripted-agent + test-tool support for the engine retry suite.

The retry tests come in two flavours:

- **Lifecycle bookkeeping** (state integrity, multi-attempt, profile matrix,
  side effects, callers). These do not need a real query loop — they
  monkeypatch ``engine.agent.factory.spawn_agent`` to return a
  :class:`ScriptedRetryAgent` whose ``run`` method replays a list of
  pre-scripted outcomes.
- **End-to-end provider stream** (``test_engine_retry_end_to_end.py``).
  These drive the real loop through a :class:`FakeProviderClient`
  (see ``_fake_provider.py``) and need real :class:`EphemeralAgent`
  instances wired against a real :class:`ToolRegistry`.

Both helpers live here so the individual test files stay focused on the
assertions for their slice of the matrix in
``backend/tests/RETRY_TESTING_PLAN.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from engine.agent.factory import EphemeralAgent
from engine.query.context import QueryContext
from message.messages import ConversationMessage, TextBlock
from message.stream_events import ToolExecutionCompleted
from providers.types import UsageSnapshot
from tools._framework.core.base import (
    BaseTool,
    ExecutionMetadata,
    ToolExecutionContextService,
)
from tools._framework.core.decorator import tool
from tools._framework.core.registry import ToolRegistry
from tools._framework.core.results import TextToolOutput, ToolResult


# ---------------------------------------------------------------------------
# ScriptedRetryAgent — bypasses the real query loop.
# ---------------------------------------------------------------------------


class ScriptedRetryAgent:
    """Test double matching the surface :func:`run_ephemeral_agent` calls.

    Each entry in *outcomes* describes one ``run()`` invocation:

    - ``events``: stream events to yield this attempt.
    - ``exit_reason``: :class:`QueryExitReason` set on ``query_context``
      before returning (mimicking the production loop's exit handling).
    - ``terminal_result``: optional :class:`ToolResult` stamped on
      ``query_context.terminal_result``.
    - ``append_messages``: messages the production loop would have
      appended to ``self._messages`` before returning. The retry path
      reads these to rebuild the next-turn transcript.
    - ``raise``: an exception to raise instead of yielding events
      (crashes are short-circuit and never retried).
    - ``usage``: usage delta added to ``total_usage`` this attempt — lets
      tests assert that ``total_usage`` accumulates across attempts.
    - ``on_run``: callback invoked at the START of the attempt with the
      agent so tests can introspect ``query_context`` state.
    """

    agent_name: str
    model: str

    def __init__(
        self,
        outcomes: list[dict[str, Any]],
        *,
        terminal_tools: set[str] | None = None,
        tool_call_limit: int | None = None,
        agent_name: str = "scripted",
        model: str = "fake-model",
        notification_state: dict[str, Any] | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.model = model
        self.total_usage = UsageSnapshot()
        self._messages: list[ConversationMessage] = []
        self.outcomes = list(outcomes)
        self.run_calls: list[dict[str, Any]] = []
        self.close_calls = 0
        self.query_context = SimpleNamespace(
            tool_metadata=ExecutionMetadata(),
            run_id="",
            terminal_result=None,
            terminal_tools=set(terminal_tools or ()),
            tool_calls_used=0,
            tool_call_limit=tool_call_limit,
            exit_reason=None,
            notification_state=dict(notification_state or {}),
        )

    @property
    def messages(self) -> list[ConversationMessage]:
        return self._messages

    async def run(self, prompt: str | None, *, auto_close: bool = True):
        self.run_calls.append(
            {
                "prompt": prompt,
                "auto_close": auto_close,
                "messages_snapshot": list(self._messages),
                "tool_calls_used_at_start": self.query_context.tool_calls_used,
                "exit_reason_at_start": self.query_context.exit_reason,
                "budget_warning_state_at_start": dict(
                    self.query_context.notification_state.get("budget_warning", {})
                ),
                "notification_state_at_start": {
                    k: dict(v) if isinstance(v, dict) else v
                    for k, v in self.query_context.notification_state.items()
                },
                "run_id_at_start": self.query_context.run_id,
                "tool_metadata_snapshot": dict(self.query_context.tool_metadata),
                "tool_call_limit_at_start": self.query_context.tool_call_limit,
            }
        )
        if prompt is not None:
            self._messages = [
                *self._messages,
                ConversationMessage.from_user_text(prompt),
            ]
        if not self.outcomes:
            return
        outcome = self.outcomes.pop(0)
        on_run: Callable[[ScriptedRetryAgent], None] | None = outcome.get("on_run")
        if on_run is not None:
            on_run(self)
        for event in outcome.get("events", []):
            yield event
        if "raise" in outcome:
            raise outcome["raise"]
        for message in outcome.get("append_messages", ()):
            self._messages = [*self._messages, message]
        self.query_context.exit_reason = outcome.get("exit_reason")
        if outcome.get("terminal_result") is not None:
            self.query_context.terminal_result = outcome["terminal_result"]
        usage = outcome.get("usage")
        if usage is not None:
            self.total_usage.input_tokens += usage.input_tokens
            self.total_usage.output_tokens += usage.output_tokens

    async def close(self) -> None:
        self.close_calls += 1


def terminal_completed_event(
    tool_name: str = "submit_x", output: str = "done"
) -> ToolExecutionCompleted:
    """Build a successful terminal :class:`ToolExecutionCompleted` event."""
    return ToolExecutionCompleted(
        tool_name=tool_name,
        output=output,
        is_error=False,
        does_terminate=True,
    )


def make_tool_result_user_message(
    text: str = "dummy_tool_results",
) -> ConversationMessage:
    """Build a placeholder user message representing the loop's tool_results.

    The production query loop appends a ``user`` message containing
    :class:`ToolResultBlock` entries before exiting on RESOURCE_LIMIT.
    For scripted tests that don't need real tool_result blocks, this
    helper supplies a stand-in :class:`TextBlock` carrier that the retry
    path can still merge with the nudge.
    """
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


# ---------------------------------------------------------------------------
# Real-loop test tools — minimal terminal + non-terminal pair for L1 e2e.
# ---------------------------------------------------------------------------


class _SubmitInput(BaseModel):
    payload: str = ""


class _ReadFileInput(BaseModel):
    path: str = ""


@tool(
    name="submit_x",
    description="Test terminal tool.",
    input_model=_SubmitInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
)
async def _submit_x_tool(
    payload: str = "",
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    del context
    return ToolResult(
        output=payload or "submitted",
        is_error=False,
        does_terminate=True,
    )


@tool(
    name="read_file_stub",
    description="Test non-terminal tool that echoes a fixed string.",
    input_model=_ReadFileInput,
    output_model=TextToolOutput,
)
async def _read_file_stub(
    path: str = "",
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    del context
    return ToolResult(output=f"contents-of:{path}", is_error=False)


def fresh_test_tools() -> list[BaseTool]:
    """Return a fresh terminal + non-terminal pair for one test.

    Tools registered with the @tool decorator are module-level singletons.
    Reusing the same instance across many :class:`ToolRegistry` builds is
    fine in unit tests (no shared mutable per-instance state on these
    stubs), but exposing them through a function keeps the call sites
    forward-compatible with future stub additions.
    """
    return [_submit_x_tool, _read_file_stub]


def build_real_loop_agent(
    api_client: Any,
    *,
    tool_call_limit: int | None = None,
    tool_calls_used: int = 0,
    terminal_tools: set[str] | None = None,
    extra_tools: list[BaseTool] | None = None,
    include_terminal_tool: bool = True,
    initial_messages: list[ConversationMessage] | None = None,
    agent_name: str = "executor",
    model: str = "fake-model",
    enable_background_tasks: bool = False,
    notification_rules: list[Any] | None = None,
) -> EphemeralAgent:
    """Build a real :class:`EphemeralAgent` wired against *api_client*.

    The query loop wires its terminal_tools from the registry when the
    caller leaves ``context.terminal_tools`` empty. Tests that want to
    suppress retry must pass ``terminal_tools=set()`` AND
    ``include_terminal_tool=False`` so the registry auto-population
    doesn't re-add ``submit_x`` from the test fixtures.
    """
    registry = ToolRegistry()
    for stub in fresh_test_tools():
        if not include_terminal_tool and stub.is_terminal_tool:
            continue
        registry.register(stub)
    if extra_tools:
        for stub in extra_tools:
            registry.register(stub)

    context = QueryContext(
        api_client=api_client,
        tool_registry=registry,
        cwd=Path("/tmp"),
        model=model,
        system_prompt="",
        max_tokens=128,
        tool_call_limit=tool_call_limit,
        tool_calls_used=tool_calls_used,
        terminal_tools=set() if terminal_tools is None else set(terminal_tools),
        tool_metadata=ExecutionMetadata(),
        enable_background_tasks=enable_background_tasks,
        notification_rules=list(notification_rules or []),
    )

    return EphemeralAgent(
        agent_name=agent_name,
        query_context=context,
        model=model,
        _messages=list(initial_messages or []),
    )


def install_scripted_agent(
    monkeypatch: Any, agent: Any
) -> None:
    """Patch :func:`engine.agent.factory.spawn_agent` to return *agent*."""
    monkeypatch.setattr(
        "engine.agent.factory.spawn_agent",
        lambda *_a, **_kw: agent,
    )
