"""Tests for ``tool_call_limit`` accounting.

After Phase 2 of the agent-loop termination refactor, ``execute_tool_call``
only counts dispatch attempts — it never rejects on budget. Hard-failure on
overshoot lives in the loop, gated on
``overshoot_units > max_tolerance_after_max_tool_call``; soft signaling is
delivered by the ``budget_overflow_reminder`` notification rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents import AgentDefinition
from engine.query.context import QueryContext
from tools._framework.execution.tool_call import execute_tool_call
from tools._framework.core.runtime import ExecutionMetadata


def _ctx(
    limit: int | None,
    used: int = 0,
    terminal_tools: set[str] | None = None,
) -> QueryContext:
    """Build a minimal QueryContext that only the budget paths inspect."""
    from unittest.mock import MagicMock

    return QueryContext(
        api_client=MagicMock(),
        tool_registry=MagicMock(),
        cwd=Path("."),
        model="m",
        system_prompt="p",
        max_tokens=1,
        tool_call_limit=limit,
        tool_calls_used=used,
        terminal_tools=terminal_tools or set(),
        tool_metadata=ExecutionMetadata(),
    )


# ---------- AgentDefinition --------------------------------------------------


def test_agent_definition_accepts_tool_call_limit():
    a = AgentDefinition(name="x", description="y", tool_call_limit=40)
    assert a.tool_call_limit == 40


def test_agent_definition_default_unlimited():
    assert AgentDefinition(name="x", description="y").tool_call_limit is None


def test_agent_definition_coerces_string():
    a = AgentDefinition.model_validate(
        {"name": "x", "description": "y", "tool_call_limit": "12"}
    )
    assert a.tool_call_limit == 12


def test_agent_definition_rejects_zero_and_negative():
    a = AgentDefinition(name="x", description="y", tool_call_limit=0)
    assert a.tool_call_limit is None
    a = AgentDefinition(name="x", description="y", tool_call_limit=-3)
    assert a.tool_call_limit is None


def test_agent_definition_rejects_removed_legacy_fields():
    with pytest.raises(ValueError):
        AgentDefinition.model_validate(
            {"name": "x", "description": "y", "effort": "high"}
        )


def test_agent_definition_default_tolerance_is_ten():
    a = AgentDefinition(name="x", description="y")
    assert a.max_tolerance_after_max_tool_call == 10


def test_agent_definition_accepts_explicit_tolerance():
    a = AgentDefinition(
        name="x", description="y", max_tolerance_after_max_tool_call=3
    )
    assert a.max_tolerance_after_max_tool_call == 3


def test_agent_definition_allows_zero_tolerance():
    # 0 is meaningful — "no grace, hard cap at exactly tool_call_limit".
    a = AgentDefinition(
        name="x", description="y", max_tolerance_after_max_tool_call=0
    )
    assert a.max_tolerance_after_max_tool_call == 0


def test_agent_definition_rejects_negative_tolerance():
    a = AgentDefinition(
        name="x", description="y", max_tolerance_after_max_tool_call=-1
    )
    assert a.max_tolerance_after_max_tool_call is None


def test_agent_definition_coerces_tolerance_string():
    a = AgentDefinition.model_validate(
        {"name": "x", "description": "y", "max_tolerance_after_max_tool_call": "7"}
    )
    assert a.max_tolerance_after_max_tool_call == 7


# ---------- execute_tool_call counter-only behavior --------------------------


@pytest.mark.asyncio
async def test_execute_tool_call_counts_past_limit_without_rejecting():
    """`tool_call_limit` is a soft threshold; never produces a budget error."""
    ctx = _ctx(limit=2, used=2)
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]

    result = await execute_tool_call(ctx, "any_tool", "id1", {})

    # The only error is "Unknown tool" (registry mock returns None) — no
    # budget rejection. Counter advances past the soft limit.
    assert "tool_call_limit" not in result.content
    assert "terminal call reserved" not in result.content
    assert ctx.tool_calls_used == 3


@pytest.mark.asyncio
async def test_execute_tool_call_increments_counter_on_unknown_tool():
    """Counting happens at dispatch attempt, before tool resolution."""
    ctx = _ctx(limit=10, used=0)
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]
    result = await execute_tool_call(ctx, "ghost", "id1", {})
    assert result.is_error
    assert "Unknown tool" in result.content
    assert ctx.tool_calls_used == 1


@pytest.mark.asyncio
async def test_execute_tool_call_does_not_reserve_last_call_for_terminal():
    """The pre-Phase-2 "reserved last call" gate is gone — non-terminal at
    `limit - 1` proceeds normally."""
    ctx = _ctx(limit=2, used=1, terminal_tools={"submit_execution_success"})
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]

    result = await execute_tool_call(ctx, "read_file", "id1", {})

    assert "terminal call reserved" not in result.content
    # Counter advances; reservation logic removed.
    assert ctx.tool_calls_used == 2


@pytest.mark.asyncio
async def test_execute_tool_call_unlimited_budget_does_not_count():
    ctx = _ctx(limit=None, used=0)
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]
    await execute_tool_call(ctx, "ghost", "id1", {})
    # ``None`` limit short-circuits the counter; counter stays put.
    assert ctx.tool_calls_used == 0


# ---------- budget warning ---------------------------------------------------
# Budget warnings (50/75/90%) fire as the ``budget_warning`` notification rule;
# overshoot reminders fire as ``budget_overflow_reminder``. Rule-level coverage
# lives in `backend/tests/unit_test/test_notification/`.
