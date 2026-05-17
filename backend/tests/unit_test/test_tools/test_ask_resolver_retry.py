"""Caller-propagation tests for ``ask_resolver`` over the engine retry path.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §2b (resolver side).

Mirrors :mod:`test_ask_advisor_retry`. Pinned error strings:
``"ask_resolver: resolver exited without submit_resolver_result."`` and
``"ask_resolver: resolver crashed: <e>"``.

After the two-user-message launch shape landed, this module additionally
asserts the resolver is spawned with ``initial_messages=[<context>]`` and
``prompt=<role_instruction + ask_section>``, and that an inherited parent
transcript block surfaces in the rendered context when
``conversation_messages`` is populated on the execution context.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents import AgentDefinition, AgentKind
from engine.agent.lifecycle import EphemeralRunResult
from message.messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.renderer import MarkdownPromptRenderer
from tools._framework.core.base import ExecutionMetadata, ToolResult
from tools._framework.core.context import ToolExecutionContextService
from tools.ask_helper.ask_resolver import ask_resolver


@dataclass
class _StubLaunchBundle:
    agent_def: AgentDefinition
    packet: ContextPacket
    context_packet_id: str | None = None


_RESOLVER_DEF = AgentDefinition(
    name="resolver",
    description="resolver stub",
    agent_type="agent",
    agent_kind=AgentKind.RESOLVER,
    context_recipe="resolver_recipe",
    terminals=["submit_resolver_result"],
)


def _make_packet() -> ContextPacket:
    return ContextPacket(
        target_role="resolver",
        canonical_refs=ContextRefs(goal_id="goal-1", task_id="resolver:abc"),
        blocks=[
            ContextBlock(
                kind=ContextBlockKind.GOAL_STATEMENT,
                priority=ContextPriority.HIGH,
                text="inherited goal",
                metadata={"inherited_from_parent": "true"},
            ),
        ],
    )


def _make_context(
    *, conversation_messages: list[ConversationMessage] | None = None
) -> ToolExecutionContextService:
    metadata = ExecutionMetadata()
    metadata.runtime_config = SimpleNamespace(cwd=Path("/tmp"))
    metadata.sandbox_id = ""
    metadata.task_center_task_id = "parent-task"
    metadata.composer = SimpleNamespace(renderer=MarkdownPromptRenderer())
    if conversation_messages is not None:
        metadata.conversation_messages = list(conversation_messages)
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


def _install_compose_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_compose(*, helper_role: str, base_agent_name: str, context: Any) -> Any:
        del helper_role, base_agent_name, context
        return _StubLaunchBundle(
            agent_def=_RESOLVER_DEF,
            packet=_make_packet(),
        )

    module = sys.modules["tools.ask_helper.ask_resolver"]
    monkeypatch.setattr(module, "compose_helper_bundle", _fake_compose)


def _install_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: EphemeralRunResult,
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    async def _fake(*args: Any, **kwargs: Any) -> EphemeralRunResult:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr("engine.api.run_ephemeral_agent", _fake, raising=False)
    monkeypatch.setattr(
        "engine.agent.lifecycle.run_ephemeral_agent", _fake, raising=False
    )
    return calls


@pytest.mark.asyncio
async def test_resolver_retry_delivers_submit_resolver_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal retry succeeds → parent gets resolver's terminal output."""
    _install_compose_stub(monkeypatch)
    terminal = ToolResult(
        output="patched the failing test",
        is_error=False,
        does_terminate=True,
        metadata={"files_touched": 3},
    )
    calls = _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=terminal,
            agent_name="resolver",
            event_count=10,
        ),
    )

    result = await ask_resolver._entrypoint(
        issues_to_resolve=["tests fail"],
        issue_context="ctx",
        context=_make_context(),
    )

    assert result.is_error is False
    assert result.output == "patched the failing test"
    assert result.metadata.get("files_touched") == 3
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_resolver_retry_exhausted_returns_pinned_error_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All internal retries failed → pinned error string returned verbatim."""
    _install_compose_stub(monkeypatch)
    _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=None,
            agent_name="resolver",
            event_count=4,
        ),
    )

    result = await ask_resolver._entrypoint(
        issues_to_resolve=["x"],
        issue_context="",
        context=_make_context(),
    )

    assert result.is_error is True
    assert result.output == (
        "ask_resolver: resolver exited without submit_resolver_result."
    )


@pytest.mark.asyncio
async def test_resolver_internal_retries_invisible_to_parent_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each parent call to ``ask_resolver`` invokes the inner runner exactly once."""
    _install_compose_stub(monkeypatch)
    success = EphemeralRunResult(
        status="completed",
        error=None,
        terminal_result=ToolResult(
            output="ok", is_error=False, does_terminate=True
        ),
        agent_name="resolver",
        event_count=1,
    )
    calls = _install_runner(monkeypatch, result=success)

    context = _make_context()
    for _ in range(5):
        await ask_resolver._entrypoint(
            issues_to_resolve=["x"],
            issue_context="",
            context=context,
        )

    assert len(calls) == 5


@pytest.mark.asyncio
async def test_resolver_crash_returns_pinned_crash_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashed inner run surfaces the pinned crash error."""
    _install_compose_stub(monkeypatch)
    _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="failed",
            error="resolver-boom",
            terminal_result=None,
            agent_name="resolver",
            event_count=0,
        ),
    )

    result = await ask_resolver._entrypoint(
        issues_to_resolve=["x"],
        issue_context="",
        context=_make_context(),
    )

    assert result.is_error is True
    assert result.output == "ask_resolver: resolver crashed: resolver-boom"


# ---- Two-user-message launch-shape assertions ------------------------------


@pytest.mark.asyncio
async def test_resolver_launches_with_two_user_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ask_resolver passes initial_messages=[<context>] + prompt=<role_instruction + ask>."""
    _install_compose_stub(monkeypatch)
    calls = _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="ok", is_error=False, does_terminate=True
            ),
            agent_name="resolver",
            event_count=1,
        ),
    )

    await ask_resolver._entrypoint(
        issues_to_resolve=["thing broke"],
        issue_context="full ctx",
        context=_make_context(),
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    initial_messages = kwargs.get("initial_messages")
    assert isinstance(initial_messages, list)
    assert len(initial_messages) == 1
    msg = initial_messages[0]
    assert isinstance(msg, ConversationMessage)
    assert msg.role == "user"
    context_text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    assert "# How to Proceed" not in context_text
    assert "inherited goal" in context_text

    prompt_arg = args[1]
    assert "# Resolver request" in prompt_arg
    # resolver_instruction() text fragment.
    assert "consult the parent transcript" in prompt_arg


@pytest.mark.asyncio
async def test_resolver_renders_parent_transcript_when_messages_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty conversation_messages list surfaces under # Parent transcript."""
    _install_compose_stub(monkeypatch)
    calls = _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="ok", is_error=False, does_terminate=True
            ),
            agent_name="resolver",
            event_count=1,
        ),
    )

    parent_msgs = [
        ConversationMessage(role="user", content=[TextBlock(text="spawn prompt")]),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(name="shell", input={"cmd": "pytest"})],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content="2 failed",
                    is_error=True,
                )
            ],
        ),
    ]
    await ask_resolver._entrypoint(
        issues_to_resolve=["tests fail"],
        issue_context="ctx",
        context=_make_context(conversation_messages=parent_msgs),
    )

    _args, kwargs = calls[0]
    msg = kwargs["initial_messages"][0]
    context_text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    # First user message (spawn prompt) is filtered out by the two-stage
    # filter; tool_use + tool_result are preserved.
    assert "# Parent transcript" in context_text
    assert "spawn prompt" not in context_text
    assert "tool_use: shell" in context_text
    assert "2 failed" in context_text


@pytest.mark.asyncio
async def test_resolver_omits_parent_transcript_when_no_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty / missing conversation_messages → no # Parent transcript heading."""
    _install_compose_stub(monkeypatch)
    calls = _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="ok", is_error=False, does_terminate=True
            ),
            agent_name="resolver",
            event_count=1,
        ),
    )

    await ask_resolver._entrypoint(
        issues_to_resolve=["x"],
        issue_context="",
        context=_make_context(),  # no conversation_messages
    )

    _args, kwargs = calls[0]
    msg = kwargs["initial_messages"][0]
    context_text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    assert "# Parent transcript" not in context_text
