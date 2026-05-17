"""Caller-propagation tests for ``ask_advisor`` over the engine retry path.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §2b (advisor side).

The retry semantics live inside :func:`run_ephemeral_agent`; from
``ask_advisor``'s perspective the contract is identical to other
ephemeral-run wrappers:

- ``terminal_result is not None`` → forward as ToolResult.
- ``terminal_result is None`` and ``status == "completed"`` → pinned error
  ``"ask_advisor: advisor exited without submit_advisor_feedback."``
- ``status == "failed"`` → pinned error ``"ask_advisor: advisor crashed: <e>"``

Additionally, after the two-user-message launch shape landed, this module
asserts the advisor is spawned with ``initial_messages=[<context>]`` and
``prompt=<role_instruction + ask_section>`` — never as a single concatenated
prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents import AgentDefinition, AgentKind
from engine.agent.lifecycle import EphemeralRunResult
from message.messages import ConversationMessage, TextBlock
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
from tools.ask_helper.ask_advisor import ask_advisor


@dataclass
class _StubLaunchBundle:
    """Minimal duck-typed stand-in for :class:`LaunchBundle`.

    Carries a real :class:`ContextPacket` so the helper can mutate
    ``bundle.packet.blocks`` post-compose (appending the advisor's own
    role_instruction) before re-rendering for the two-user-message launch.
    """

    agent_def: AgentDefinition
    packet: ContextPacket
    context_packet_id: str | None = None


_ADVISOR_DEF = AgentDefinition(
    name="advisor",
    description="advisor stub",
    agent_type="agent",
    agent_kind=AgentKind.ADVISOR,
    context_recipe="advisor_recipe",
    terminals=["submit_advisor_feedback"],
)


def _make_packet() -> ContextPacket:
    return ContextPacket(
        target_role="advisor",
        canonical_refs=ContextRefs(goal_id="goal-1", task_id="advisor:abc"),
        blocks=[
            ContextBlock(
                kind=ContextBlockKind.GOAL_STATEMENT,
                priority=ContextPriority.HIGH,
                text="inherited goal",
                metadata={"inherited_from_parent": "true"},
            ),
        ],
    )


def _make_context() -> ToolExecutionContextService:
    metadata = ExecutionMetadata()
    metadata.runtime_config = SimpleNamespace(cwd=Path("/tmp"))
    metadata.sandbox_id = ""
    metadata.task_center_task_id = "parent-task"
    # The advisor reads composer.renderer to render the bundle packet into
    # the two separate user messages. Wire a real renderer so the test
    # exercises the production rendering pipeline.
    metadata.composer = SimpleNamespace(renderer=MarkdownPromptRenderer())
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


def _install_compose_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_compose(*, helper_role: str, base_agent_name: str, context: Any) -> Any:
        del helper_role, base_agent_name, context
        return _StubLaunchBundle(
            agent_def=_ADVISOR_DEF,
            packet=_make_packet(),
        )

    # ``tools.ask_helper.__init__`` re-exports the FunctionTool under the
    # name ``ask_advisor`` so a string-path monkeypatch resolves to the
    # tool instance rather than the module. Use the explicit module
    # object from ``sys.modules`` to attach the stub.
    import sys
    module = sys.modules["tools.ask_helper.ask_advisor"]
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
async def test_advisor_retry_delivers_submit_advisor_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal retry succeeds → parent receives advisor's terminal output."""
    _install_compose_stub(monkeypatch)
    terminal = ToolResult(
        output="advisor recommends X",
        is_error=False,
        does_terminate=True,
        metadata={"score": 7},
    )
    calls = _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=terminal,
            agent_name="advisor",
            event_count=5,
        ),
    )

    result = await ask_advisor._entrypoint(
        tool_name="submit_plan_closes_goal",
        tool_payloads=[{"k": "v"}],
        prompt="should I?",
        context=_make_context(),
    )

    assert result.is_error is False
    assert result.output == "advisor recommends X"
    assert result.metadata.get("score") == 7
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_advisor_retry_exhausted_returns_pinned_error_string(
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
            agent_name="advisor",
            event_count=2,
        ),
    )

    result = await ask_advisor._entrypoint(
        tool_name="submit_plan_closes_goal",
        tool_payloads=[],
        prompt="anything",
        context=_make_context(),
    )

    assert result.is_error is True
    assert result.output == (
        "ask_advisor: advisor exited without submit_advisor_feedback."
    )


@pytest.mark.asyncio
async def test_advisor_internal_retries_invisible_to_parent_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each parent call to ``ask_advisor`` invokes the inner runner exactly once."""
    _install_compose_stub(monkeypatch)
    success = EphemeralRunResult(
        status="completed",
        error=None,
        terminal_result=ToolResult(
            output="ok", is_error=False, does_terminate=True
        ),
        agent_name="advisor",
        event_count=1,
    )
    calls = _install_runner(monkeypatch, result=success)

    context = _make_context()
    for _ in range(4):
        await ask_advisor._entrypoint(
            tool_name="submit_plan_closes_goal",
            tool_payloads=[],
            prompt="x",
            context=context,
        )

    # Four parent calls → four inner invocations. Internal retries (if
    # any) are absorbed inside each run_ephemeral_agent call.
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_advisor_crash_returns_pinned_crash_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashed inner run surfaces the pinned crash error."""
    _install_compose_stub(monkeypatch)
    _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="failed",
            error="downstream-boom",
            terminal_result=None,
            agent_name="advisor",
            event_count=0,
        ),
    )

    result = await ask_advisor._entrypoint(
        tool_name="submit_plan_closes_goal",
        tool_payloads=[],
        prompt="x",
        context=_make_context(),
    )

    assert result.is_error is True
    assert result.output == "ask_advisor: advisor crashed: downstream-boom"


# ---- Two-user-message launch-shape assertions ------------------------------


@pytest.mark.asyncio
async def test_advisor_launches_with_two_user_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ask_advisor passes initial_messages=[<context>] + prompt=<role_instruction + ask>."""
    _install_compose_stub(monkeypatch)
    calls = _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="ok", is_error=False, does_terminate=True
            ),
            agent_name="advisor",
            event_count=1,
        ),
    )

    await ask_advisor._entrypoint(
        tool_name="submit_plan_closes_goal",
        tool_payloads=[{"k": "v"}],
        prompt="prompt body",
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
    assert all(isinstance(b, TextBlock) for b in msg.content)
    context_text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    assert "# How to Proceed" not in context_text
    assert "inherited goal" in context_text  # rendered context contains the inherited block

    # args[1] is the spawn prompt (role_instruction + ask_section).
    prompt_arg = args[1]
    assert "# Advisor request" in prompt_arg
    # role_instruction text from advisor_instruction(tool_name="submit_plan_closes_goal")
    assert "planner submission that proposes to CLOSE" in prompt_arg


@pytest.mark.asyncio
async def test_advisor_falls_back_to_default_role_instruction_on_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown tool_name still produces a role_instruction (the default)."""
    _install_compose_stub(monkeypatch)
    calls = _install_runner(
        monkeypatch,
        result=EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="ok", is_error=False, does_terminate=True
            ),
            agent_name="advisor",
            event_count=1,
        ),
    )

    await ask_advisor._entrypoint(
        tool_name="never_seen_terminal_name",
        tool_payloads=[],
        prompt="x",
        context=_make_context(),
    )

    _args, kwargs = calls[0]
    assert kwargs.get("initial_messages") is not None
    # _ADVISOR_DEFAULT text fragment.
    assert "Review the proposed tool name and payload" in calls[0][0][1]
