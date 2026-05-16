"""Caller-propagation tests for ``ask_advisor`` over the engine retry path.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §2b (advisor side).

The retry semantics live inside :func:`run_ephemeral_agent`; from
``ask_advisor``'s perspective the contract is identical to other
ephemeral-run wrappers:

- ``terminal_result is not None`` → forward as ToolResult.
- ``terminal_result is None`` and ``status == "completed"`` → pinned error
  ``"ask_advisor: advisor exited without submit_advisor_feedback."``
- ``status == "failed"`` → pinned error ``"ask_advisor: advisor crashed: <e>"``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents import AgentDefinition, AgentKind
from engine.agent.lifecycle import EphemeralRunResult
from tools._framework.core.base import ExecutionMetadata, ToolResult
from tools._framework.core.context import ToolExecutionContextService
from tools.ask_helper.ask_advisor import ask_advisor


@dataclass(frozen=True, slots=True)
class _StubLaunchBundle:
    """Minimal duck-typed stand-in for :class:`LaunchBundle`."""

    agent_def: AgentDefinition
    rendered_prompt: str
    context_packet_id: str | None = None


_ADVISOR_DEF = AgentDefinition(
    name="advisor",
    description="advisor stub",
    agent_type="agent",
    agent_kind=AgentKind.ADVISOR,
    context_recipe="advisor_recipe",
    terminals=["submit_advisor_feedback"],
)


def _make_context() -> ToolExecutionContextService:
    metadata = ExecutionMetadata()
    metadata.runtime_config = SimpleNamespace(cwd=Path("/tmp"))
    metadata.sandbox_id = ""
    metadata.task_center_task_id = "parent-task"
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


def _install_compose_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_compose(*, helper_role: str, base_agent_name: str, context: Any) -> Any:
        del helper_role, base_agent_name, context
        return _StubLaunchBundle(
            agent_def=_ADVISOR_DEF,
            rendered_prompt="ADVISOR_PROMPT",
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
        tool_name="submit_x",
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
        tool_name="submit_x",
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
            tool_name="submit_x",
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
        tool_name="submit_x",
        tool_payloads=[],
        prompt="x",
        context=_make_context(),
    )

    assert result.is_error is True
    assert result.output == "ask_advisor: advisor crashed: downstream-boom"
