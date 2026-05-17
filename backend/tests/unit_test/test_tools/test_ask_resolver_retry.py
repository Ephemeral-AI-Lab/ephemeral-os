"""Caller-propagation tests for ``ask_resolver`` over the engine retry path.

Mirrors :mod:`test_ask_advisor_retry`. Pinned error strings:
``"ask_resolver: resolver exited without submit_resolver_result."`` and
``"ask_resolver: resolver crashed: <e>"``. Also asserts the two-user-
message launch shape with the parent transcript surfacing under
``# Parent transcript`` in resolver mode (keeps tool inputs verbatim).
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
from tools._framework.core.base import ExecutionMetadata, ToolResult
from tools._framework.core.context import ToolExecutionContextService
from tools.ask_helper.ask_resolver import ask_resolver


_RESOLVER_DEF = AgentDefinition(
    name="resolver",
    description="resolver stub",
    agent_type="agent",
    agent_kind=AgentKind.RESOLVER,
    terminals=["submit_resolver_result"],
)

_PARENT_VERIFIER_DEF = AgentDefinition(
    name="verifier",
    description="parent verifier stub",
    agent_type="agent",
    agent_kind=AgentKind.VERIFIER,
    terminals=["submit_verification_success", "submit_verification_failure"],
)


@dataclass(frozen=True, slots=True)
class _HelperMessagesStub:
    helper_agent_def: AgentDefinition
    parent_agent_def: AgentDefinition | None
    parent_user_msg_1: str
    parent_user_msg_2: str
    parent_transcript: str | None


def _make_context(
    *, conversation_messages: list[ConversationMessage] | None = None
) -> ToolExecutionContextService:
    metadata = ExecutionMetadata()
    metadata.runtime_config = SimpleNamespace(cwd=Path("/tmp"))
    metadata.sandbox_id = ""
    metadata.agent_name = "verifier"
    metadata.task_center_task_id = "parent-task"
    if conversation_messages is not None:
        metadata.conversation_messages = list(conversation_messages)
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


def _install_build_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transcript: str | None = "## role:assistant\n\nparent did some work",
) -> None:
    def _fake_build(
        *, helper_role: str, mode: str, context: Any
    ) -> _HelperMessagesStub:
        del helper_role, mode, context
        return _HelperMessagesStub(
            helper_agent_def=_RESOLVER_DEF,
            parent_agent_def=_PARENT_VERIFIER_DEF,
            parent_user_msg_1="parent context here",
            parent_user_msg_2="parent task here",
            parent_transcript=transcript,
        )

    module = sys.modules["tools.ask_helper.ask_resolver"]
    monkeypatch.setattr(module, "build_helper_messages", _fake_build)


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


# ---- Outcome-propagation -----------------------------------------------


@pytest.mark.asyncio
async def test_resolver_returns_terminal_output_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_build_stub(monkeypatch)
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
async def test_resolver_returns_pinned_error_when_terminal_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_build_stub(monkeypatch)
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
async def test_resolver_returns_pinned_error_on_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_build_stub(monkeypatch)
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


# ---- Two-user-message launch-shape assertions --------------------------


@pytest.mark.asyncio
async def test_resolver_launches_with_two_user_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_build_stub(monkeypatch)
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
    context_text = "".join(
        b.text for b in msg.content if isinstance(b, TextBlock)
    )
    assert "# Parent agent's original context" in context_text
    assert "# Parent agent's original task" in context_text
    assert "# Parent transcript" in context_text
    assert "# Parent context" not in context_text  # inheritance heading gone

    user_msg_2 = args[1]
    assert "# Issues to resolve" in user_msg_2
    assert "thing broke" in user_msg_2
    assert "full ctx" in user_msg_2
    assert "submit_resolver_result" in user_msg_2


@pytest.mark.asyncio
async def test_resolver_omits_parent_transcript_when_no_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_build_stub(monkeypatch, transcript=None)
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
        context=_make_context(),
    )

    _args, kwargs = calls[0]
    msg = kwargs["initial_messages"][0]
    context_text = "".join(
        b.text for b in msg.content if isinstance(b, TextBlock)
    )
    assert "# Parent transcript" not in context_text


@pytest.mark.asyncio
async def test_resolver_transcript_keeps_tool_use_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver mode preserves tool inputs (verifier needs them to debug)."""
    transcript = (
        "## role:assistant\n\n"
        "## tool_use: Bash\n\n"
        '```json\n{"command": "pytest -x"}\n```\n\n'
        "## tool_result\n\n2 failed"
    )
    _install_build_stub(monkeypatch, transcript=transcript)
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
        issues_to_resolve=["tests fail"],
        issue_context="ctx",
        context=_make_context(
            conversation_messages=[
                ConversationMessage(
                    role="user", content=[TextBlock(text="spawn prompt")]
                ),
                ConversationMessage(
                    role="assistant",
                    content=[ToolUseBlock(name="Bash", input={"command": "pytest -x"})],
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
        ),
    )

    _args, kwargs = calls[0]
    msg = kwargs["initial_messages"][0]
    context_text = "".join(
        b.text for b in msg.content if isinstance(b, TextBlock)
    )
    assert "pytest -x" in context_text
