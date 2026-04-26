"""Tests for the mode authorization gate (US-007).

Covers the decision order in ``evaluate_mode_gate``, the deny-payload format,
budget non-consumption on deny, and the ``mode_transition`` signal flowing
back into ``QueryContext.active_mode``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agents.types import AgentDefinition, ModeDefinition
from message.messages import ToolResultBlock
from tools.core.base import (
    BaseTool,
    ToolRegistry,
    ToolResult,
)
from tools.core.runtime import ExecutionMetadata
from tools.core.tool_execution import (
    evaluate_mode_gate,
    execute_tool_call_streaming,
)


# --------------------------------------------------------------------------- #
# evaluate_mode_gate — decision order                                         #
# --------------------------------------------------------------------------- #


def test_gate_allows_when_active_mode_is_none() -> None:
    assert evaluate_mode_gate(None, "anything", "id-1") is None


def test_gate_denylist_wins_over_allowlist() -> None:
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        allowed_tools=["foo"],  # foo is on the allowlist...
        disallowed_tools=["foo"],  # ... but also denied
        terminals=["submit_task_completion"],
    )
    deny = evaluate_mode_gate(direct, "foo", "id-1")
    assert deny is not None and deny.is_error


def test_gate_terminal_always_allowed() -> None:
    plan = ModeDefinition(
        name="plan",
        allowed_tools=[],
        terminals=["submit_plan_handoff"],
        entry_tool="enter_plan",
        briefing="b",
    )
    assert evaluate_mode_gate(plan, "submit_plan_handoff", "id-1") is None


def test_gate_entry_tool_always_allowed() -> None:
    plan = ModeDefinition(
        name="plan",
        allowed_tools=[],
        terminals=["submit_plan_handoff"],
        entry_tool="enter_plan",
        briefing="b",
    )
    assert evaluate_mode_gate(plan, "enter_plan", "id-1") is None


def test_gate_open_toolset_when_allowed_is_none() -> None:
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        allowed_tools=None,
        terminals=["submit_task_completion"],
    )
    assert evaluate_mode_gate(direct, "anything", "id-1") is None
    assert evaluate_mode_gate(direct, "ANYTHING_ELSE", "id-2") is None


def test_gate_allowed_tools_list_gates_unknown() -> None:
    plan = ModeDefinition(
        name="plan",
        allowed_tools=["read"],
        terminals=["submit_plan"],
        entry_tool="enter_plan",
        briefing="b",
    )
    assert evaluate_mode_gate(plan, "read", "id-1") is None
    deny = evaluate_mode_gate(plan, "write", "id-1")
    assert deny is not None and deny.is_error


def test_gate_deny_payload_format() -> None:
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        allowed_tools=None,
        disallowed_tools=["submit_plan_handoff"],
        terminals=["submit_task_completion"],
    )
    deny = evaluate_mode_gate(direct, "submit_plan_handoff", "id-1")
    assert deny is not None
    assert "submit_plan_handoff" in deny.content
    assert "direct" in deny.content
    assert "submit_task_completion" in deny.content
    assert deny.is_error
    assert deny.tool_use_id == "id-1"


# --------------------------------------------------------------------------- #
# Budget non-consumption on deny                                              #
# --------------------------------------------------------------------------- #


class _NoopInput(BaseModel):
    pass


class _AllowedTool(BaseTool):
    name = "allowed_tool"
    description = "ok"
    input_model = _NoopInput

    async def execute(self, args, ctx):  # type: ignore[override]
        return ToolResult(output="ran")


@dataclass
class _StubContext:
    """Just enough of QueryContext for execute_tool_call_streaming to run."""

    tool_registry: ToolRegistry
    cwd: Path
    tool_call_limit: int | None
    tool_calls_used: int = 0
    terminal_tools: set = None  # type: ignore[assignment]
    tool_metadata: ExecutionMetadata = None  # type: ignore[assignment]
    active_mode: ModeDefinition | None = None
    agent_def: AgentDefinition | None = None

    def __post_init__(self) -> None:
        if self.terminal_tools is None:
            self.terminal_tools = set()
        if self.tool_metadata is None:
            self.tool_metadata = ExecutionMetadata()


@pytest.mark.asyncio
async def test_mode_deny_does_not_consume_budget() -> None:
    plan = ModeDefinition(
        name="plan",
        allowed_tools=["read"],
        terminals=["submit_plan_handoff"],
        entry_tool="enter_plan",
        briefing="b",
    )
    registry = ToolRegistry()
    registry.register(_AllowedTool())
    ctx = _StubContext(
        tool_registry=registry,
        cwd=Path("/tmp"),
        tool_call_limit=5,
        active_mode=plan,
    )

    async def _emit(_event):
        pass

    # `unauthorized_tool` is NOT in plan's allowed/terminals/entry — denied.
    res = await execute_tool_call_streaming(
        ctx,  # type: ignore[arg-type]
        "unauthorized_tool",
        "tu-1",
        {},
        emit=_emit,
        emit_started=False,
    )
    assert isinstance(res, ToolResultBlock)
    assert res.is_error
    assert "not allowed" in res.content
    assert ctx.tool_calls_used == 0  # budget untouched


@pytest.mark.asyncio
async def test_allowed_tool_consumes_budget() -> None:
    plan = ModeDefinition(
        name="plan",
        allowed_tools=["allowed_tool"],
        terminals=["submit_plan"],
        entry_tool="enter_plan",
        briefing="b",
    )
    registry = ToolRegistry()
    registry.register(_AllowedTool())
    ctx = _StubContext(
        tool_registry=registry,
        cwd=Path("/tmp"),
        tool_call_limit=5,
        active_mode=plan,
    )

    async def _emit(_event):
        pass

    res = await execute_tool_call_streaming(
        ctx,  # type: ignore[arg-type]
        "allowed_tool",
        "tu-1",
        {},
        emit=_emit,
        emit_started=False,
    )
    assert not res.is_error
    assert ctx.tool_calls_used == 1  # budget consumed exactly once


# --------------------------------------------------------------------------- #
# mode_transition flowing back into context.active_mode                       #
# --------------------------------------------------------------------------- #


def test_mode_transition_signal_updates_active_mode() -> None:
    """End-to-end of the mode_transition signal: a ToolResultBlock carrying
    ``mode_transition`` should let the loop's transition step swap the mode.

    We test the apply step directly (mirroring the loop's logic) rather than
    spinning up the full async loop.
    """
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        allowed_tools=None,
        terminals=["submit_task_completion"],
    )
    plan = ModeDefinition(
        name="plan_for_handoff",
        allowed_tools=["read"],
        terminals=["submit_plan_handoff"],
        entry_tool="enter_plan_for_handoff",
        briefing="b",
    )
    agent_def = AgentDefinition(
        name="ex", description="d", modes=[direct, plan]
    )
    ctx = SimpleNamespace(agent_def=agent_def, active_mode=direct)
    tool_results = [
        ToolResultBlock(tool_use_id="t1", content="b", mode_transition="plan_for_handoff"),
    ]

    # Mirror the dispatcher's apply step from query.py.
    for tr in tool_results:
        if tr.mode_transition:
            ctx.active_mode = ctx.agent_def.modes_by_name[tr.mode_transition]

    assert ctx.active_mode is plan


def test_mode_transition_unknown_is_ignored_via_get() -> None:
    """The dispatcher uses ``modes_by_name.get`` so an unknown name is a no-op."""
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        terminals=["submit_task_completion"],
    )
    agent_def = AgentDefinition(name="ex", description="d", modes=[direct])
    ctx = SimpleNamespace(agent_def=agent_def, active_mode=direct)

    next_mode = agent_def.modes_by_name.get("does_not_exist")
    assert next_mode is None
    if next_mode is not None:  # pragma: no cover (defensive guard)
        ctx.active_mode = next_mode

    assert ctx.active_mode is direct


# --------------------------------------------------------------------------- #
# ToolResult / ToolResultBlock fields                                         #
# --------------------------------------------------------------------------- #


def test_tool_result_mode_transition_default_none() -> None:
    r = ToolResult(output="x")
    assert r.mode_transition is None


def test_tool_result_block_mode_transition_default_none() -> None:
    block = ToolResultBlock(tool_use_id="t1", content="x")
    assert block.mode_transition is None


def test_tool_result_with_mode_transition_carries_value() -> None:
    r = ToolResult(output="b", mode_transition="plan_for_handoff")
    assert r.mode_transition == "plan_for_handoff"
