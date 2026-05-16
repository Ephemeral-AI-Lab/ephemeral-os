"""Tests for ``tool_call_limit`` enforcement.

The engine loop is integration-heavy, so these tests target the small,
pure helpers around query budgeting and tool execution. ``execute_tool_call``
counts every dispatch attempt and rejects with a structured error once the
cap is reached.
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


# ---------- execute_tool_call budget enforcement -----------------------------


@pytest.mark.asyncio
async def test_execute_tool_call_rejects_when_over_budget():
    ctx = _ctx(limit=2, used=2)
    result = await execute_tool_call(ctx, "any_tool", "id1", {})
    assert result.is_error
    assert "tool_call_limit exceeded" in result.content
    # Counter is NOT advanced past the cap on rejection.
    assert ctx.tool_calls_used == 2


@pytest.mark.asyncio
async def test_execute_tool_call_increments_counter_on_unknown_tool():
    """Counting happens at dispatch attempt, before tool resolution."""
    ctx = _ctx(limit=10, used=0)
    # The mock tool registry returns None → "Unknown tool" path. The
    # counter should still have incremented because dispatch was attempted.
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]
    result = await execute_tool_call(ctx, "ghost", "id1", {})
    assert result.is_error
    assert "Unknown tool" in result.content
    assert ctx.tool_calls_used == 1


@pytest.mark.asyncio
async def test_execute_tool_call_allows_terminal_tool_when_budget_exhausted():
    ctx = _ctx(limit=2, used=2, terminal_tools={"submit_plan_closes_goal"})
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]
    result = await execute_tool_call(ctx, "submit_plan_closes_goal", "id1", {})

    assert result.is_error
    assert "Unknown tool" in result.content
    assert "tool_call_limit exceeded" not in result.content
    assert ctx.tool_calls_used == 2


@pytest.mark.asyncio
async def test_execute_tool_call_reserves_last_call_for_terminal_tool():
    ctx = _ctx(limit=2, used=1, terminal_tools={"submit_execution_success"})
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]

    result = await execute_tool_call(ctx, "read_file", "id1", {})

    assert result.is_error
    assert "terminal call reserved" in result.content
    assert "submit_execution_success" in result.content
    assert ctx.tool_calls_used == 1


@pytest.mark.asyncio
async def test_execute_tool_call_unlimited_budget_does_not_count():
    ctx = _ctx(limit=None, used=0)
    ctx.tool_registry.get = lambda _name: None  # type: ignore[method-assign]
    await execute_tool_call(ctx, "ghost", "id1", {})
    # ``None`` limit short-circuits the budget gate; counter stays put.
    assert ctx.tool_calls_used == 0


# ---------- budget warning ---------------------------------------------------
# The imperative budget-warning notification was removed from tool_execution.
# Budget warnings now fire as a notification rule (see
# `backend/src/notification/library/budget_warning.py`) evaluated by
# `dispatch_rules` in the query loop. Rule-level coverage lives in
# `backend/tests/test_notification/`.
