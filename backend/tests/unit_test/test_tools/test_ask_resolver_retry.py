"""Caller-propagation tests for ``ask_resolver`` over the engine retry path.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §2b (resolver side).

Mirrors :mod:`test_ask_advisor_retry`. Pinned error strings:
``"ask_resolver: resolver exited without submit_resolver_result."`` and
``"ask_resolver: resolver crashed: <e>"``.
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
from tools._framework.core.base import ExecutionMetadata, ToolResult
from tools._framework.core.context import ToolExecutionContextService
from tools.ask_helper.ask_resolver import ask_resolver


@dataclass(frozen=True, slots=True)
class _StubLaunchBundle:
    agent_def: AgentDefinition
    rendered_prompt: str
    context_packet_id: str | None = None


_RESOLVER_DEF = AgentDefinition(
    name="resolver",
    description="resolver stub",
    agent_type="agent",
    agent_kind=AgentKind.RESOLVER,
    context_recipe="resolver_recipe",
    terminals=["submit_resolver_result"],
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
            agent_def=_RESOLVER_DEF,
            rendered_prompt="RESOLVER_PROMPT",
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
