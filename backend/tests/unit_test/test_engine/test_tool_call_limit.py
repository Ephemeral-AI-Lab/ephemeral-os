"""Tests for ``tool_call_limit`` accounting and AgentDefinition invariants.

``execute_tool_call`` only counts dispatch attempts — it never rejects on
budget. Hard-failure on overshoot lives in the loop, gated on
``tool_calls_used >= ceil(1.5 * tool_call_limit)``; soft signaling is
delivered by the ``terminal_call_reminder`` notification rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agents import AgentDefinition
from engine.query.context import QueryContext
from tools._framework.execution.tool_call import execute_tool_call
from tools._framework.core.runtime import ExecutionMetadata


def _ctx(
    limit: int,
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
    a = AgentDefinition(
        name="x", description="y", tool_call_limit=40, terminals=["submit_x"]
    )
    assert a.tool_call_limit == 40


def test_agent_definition_requires_tool_call_limit():
    with pytest.raises(ValidationError):
        AgentDefinition(name="x", description="y", terminals=["submit_x"])


def test_agent_definition_coerces_string():
    a = AgentDefinition.model_validate(
        {
            "name": "x",
            "description": "y",
            "tool_call_limit": "12",
            "terminals": ["submit_x"],
        }
    )
    assert a.tool_call_limit == 12


def test_agent_definition_rejects_zero_and_negative():
    with pytest.raises(ValidationError):
        AgentDefinition(
            name="x", description="y", tool_call_limit=0, terminals=["submit_x"]
        )
    with pytest.raises(ValidationError):
        AgentDefinition(
            name="x", description="y", tool_call_limit=-3, terminals=["submit_x"]
        )


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
