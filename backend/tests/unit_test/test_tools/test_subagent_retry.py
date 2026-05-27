"""Caller-propagation tests for ``run_subagent`` over the new engine retry.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §2a rows 1-5.

The retry path lives *inside* :func:`run_ephemeral_agent`, so these tests
monkeypatch that callable at the ``engine.api`` re-export seam and
verify only the visible contract: how the subagent's outcome is
projected onto the parent's :class:`ToolResult`. The pinned error
strings asserted here are the exact ones consumers grep for in
production audits — see ``run_subagent.py:234,239``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents import (
    AgentDefinition,
    AgentKind,
    register_definition,
    unregister_definition,
)
from engine.agent.lifecycle import EphemeralRunResult
from tools._framework.core.base import ExecutionMetadata, ToolResult
from tools._framework.core.context import ToolExecutionContextService
from tools.subagent.run_subagent import run_subagent


@pytest.fixture
def fake_subagent_definition() -> Any:
    """Register a minimal subagent definition; cleaned up afterwards."""
    name = "test_explorer_for_retry"
    register_definition(
        AgentDefinition(
            name=name,
            description="Test subagent used by subagent_retry suite.",
            agent_type="subagent",
            agent_kind=AgentKind.EXPLORER,
            context_recipe="subagent_recipe",
            terminals=["submit_exploration_result"],
            tool_call_limit=10,
        )
    )
    try:
        yield name
    finally:
        unregister_definition(name)


def _make_context(**extras: Any) -> ToolExecutionContextService:
    """Build a tool-execution context the run_subagent body will accept."""
    metadata = ExecutionMetadata()
    metadata.runtime_config = SimpleNamespace(cwd=Path("/tmp"))
    metadata.sandbox_id = ""
    for k, v in extras.items():
        metadata[k] = v
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


def _install_fake_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_value: EphemeralRunResult | Exception | None = None,
    results_per_call: list[EphemeralRunResult] | None = None,
) -> list[tuple[Any, ...]]:
    """Patch :func:`engine.api.run_ephemeral_agent` to a scripted fake.

    Returns a mutable list onto which each call's ``(args, kwargs)`` is
    appended so tests can assert how many internal calls happened and
    what they looked like.
    """
    calls: list[tuple[Any, ...]] = []
    queued = list(results_per_call or [])

    async def _fake(*args: Any, **kwargs: Any) -> EphemeralRunResult:
        calls.append((args, kwargs))
        if queued:
            outcome = queued.pop(0)
        else:
            outcome = return_value
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise AssertionError("fake runner has no scripted outcome left")
        return outcome

    monkeypatch.setattr("engine.api.run_ephemeral_agent", _fake, raising=False)
    # Also patch at the source-of-truth path since ``engine.api`` uses a
    # lazy __getattr__ proxy — if the cache has not been hit before this
    # call site, the in-function ``from engine.api import ...`` resolves
    # via __getattr__ which forwards to engine.agent.lifecycle.
    monkeypatch.setattr(
        "engine.agent.lifecycle.run_ephemeral_agent", _fake, raising=False
    )
    return calls


@pytest.mark.asyncio
async def test_subagent_retry_succeeds_then_returns_terminal_to_parent(
    monkeypatch: pytest.MonkeyPatch, fake_subagent_definition: str
) -> None:
    """Internal retry succeeded — parent sees the terminal output + marker."""
    terminal = ToolResult(
        output="exploration findings",
        is_error=False,
        metadata={"foo": "bar"},
        is_terminal=True,
    )
    success_result = EphemeralRunResult(
        status="completed",
        error=None,
        terminal_result=terminal,
        agent_name=fake_subagent_definition,
        event_count=7,
    )
    calls = _install_fake_runner(monkeypatch, return_value=success_result)

    context = _make_context()
    result = await run_subagent._entrypoint(
        agent_name=fake_subagent_definition,
        prompt="explore the codebase",
        context=context,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.output == "exploration findings"
    assert result.metadata.get("subagent_terminal_called") is True
    assert result.metadata.get("foo") == "bar"
    # The subagent path makes exactly one call to run_ephemeral_agent —
    # the retry happens INSIDE that call, invisible to the parent.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_subagent_retry_exhausted_returns_existing_error_to_parent(
    monkeypatch: pytest.MonkeyPatch, fake_subagent_definition: str
) -> None:
    """Internal retries all failed — parent sees the pinned error string."""
    exhausted_result = EphemeralRunResult(
        status="completed",
        error=None,
        terminal_result=None,  # Both attempts inside run_ephemeral_agent failed
        agent_name=fake_subagent_definition,
        event_count=4,
    )
    calls = _install_fake_runner(monkeypatch, return_value=exhausted_result)

    context = _make_context()
    result = await run_subagent._entrypoint(
        agent_name=fake_subagent_definition,
        prompt="explore",
        context=context,
    )

    assert result.is_error is True
    # Pinned error string — assertion intentionally exact.
    assert result.output == (
        "run_subagent: subagent exited without calling a terminal tool. "
        "The findings were not delivered."
    )
    assert result.metadata.get("subagent_terminal_called") is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_subagent_internal_retries_invisible_to_parent_budget(
    monkeypatch: pytest.MonkeyPatch, fake_subagent_definition: str
) -> None:
    """Each parent call to ``run_subagent`` invokes the inner runner exactly once."""
    success = EphemeralRunResult(
        status="completed",
        error=None,
        terminal_result=ToolResult(
            output="ok", is_error=False, is_terminal=True
        ),
        agent_name=fake_subagent_definition,
        event_count=1,
    )
    calls = _install_fake_runner(
        monkeypatch, results_per_call=[success, success, success]
    )

    context = _make_context()
    for _ in range(3):
        await run_subagent._entrypoint(
            agent_name=fake_subagent_definition,
            prompt="explore",
            context=context,
        )

    # Three parent calls → three inner-runner invocations. The fact that
    # each inner runner may have retried internally is the engine's
    # concern — at this seam it's one budget unit per parent call.
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_parallel_subagents_retry_independently(
    monkeypatch: pytest.MonkeyPatch, fake_subagent_definition: str
) -> None:
    """Two parallel run_subagent invocations don't cross-contaminate."""

    async def _fake(*args: Any, **kwargs: Any) -> EphemeralRunResult:
        # P2.2: the caller's prompt is now passed via initial_messages[0];
        # args[1] is the static explorer role-instruction text.
        initial = kwargs.get("initial_messages") or []
        prompt = ""
        if initial:
            prompt = "".join(
                getattr(block, "text", "") for block in initial[0].content
            )
        # Yield control to interleave the two coroutines.
        await asyncio.sleep(0)
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output=f"finding-for-{prompt}",
                is_error=False,
                is_terminal=True,
            ),
            agent_name=fake_subagent_definition,
            event_count=2,
        )

    monkeypatch.setattr("engine.api.run_ephemeral_agent", _fake, raising=False)
    monkeypatch.setattr(
        "engine.agent.lifecycle.run_ephemeral_agent", _fake, raising=False
    )

    context = _make_context()

    async def _spawn(label: str) -> ToolResult:
        return await run_subagent._entrypoint(
            agent_name=fake_subagent_definition,
            prompt=label,
            context=context,
        )

    a, b = await asyncio.gather(_spawn("alpha"), _spawn("beta"))

    assert a.output == "finding-for-alpha"
    assert b.output == "finding-for-beta"
    # Each result carries its own metadata flag — neither leaked.
    assert a.metadata.get("subagent_terminal_called") is True
    assert b.metadata.get("subagent_terminal_called") is True


@pytest.mark.asyncio
async def test_subagent_crash_does_not_trigger_retry(
    monkeypatch: pytest.MonkeyPatch, fake_subagent_definition: str
) -> None:
    """A crashed inner run surfaces the pinned crash error without retry."""
    crash_result = EphemeralRunResult(
        status="failed",
        error="downstream-boom",
        terminal_result=None,
        agent_name=fake_subagent_definition,
        event_count=0,
    )
    calls = _install_fake_runner(monkeypatch, return_value=crash_result)

    context = _make_context()
    result = await run_subagent._entrypoint(
        agent_name=fake_subagent_definition,
        prompt="explore",
        context=context,
    )

    assert result.is_error is True
    assert result.output == "run_subagent: subagent crashed: downstream-boom"
    assert result.metadata.get("subagent_terminal_called") is False
    # Crashed runs short-circuit retry inside the engine; the subagent
    # caller sees exactly one inner invocation.
    assert len(calls) == 1
