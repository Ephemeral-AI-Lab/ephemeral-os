"""Registration and schema checks for Phase 03 submission tools."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents import AgentDefinition
from agents import AgentRole
from tools._framework.factory import ToolFactoryContext, create_tool, has_tool
from tools.submission.planner import PlanTaskInput


PHASE03_TOOLS = (
    "submit_plan_closes_goal",
    "submit_plan_defers_goal",
    "submit_workflow_handoff",
    "submit_execution_success",
    "submit_execution_blocker",
    "submit_reduction_success",
    "submit_reduction_failure",
    "ask_advisor",
    "submit_advisor_feedback",
    "submit_exploration_result",
)


def test_submission_tools_registered() -> None:
    assert all(has_tool(name) for name in PHASE03_TOOLS)


def test_tool_registry_renamed() -> None:
    """PR 1 acceptance tripwire: planner-defers tool name is new, old is gone."""
    assert has_tool("submit_plan_defers_goal")
    assert has_tool("submit_plan_closes_goal")
    assert not has_tool("submit_plan_continues_goal")
    assert not has_tool("submit_execution_failure")


def test_submission_tools_are_terminal_except_helper_requests() -> None:
    non_terminal = {"ask_advisor"}
    ctx = ToolFactoryContext()

    for name in PHASE03_TOOLS:
        tool = create_tool(name, ctx)
        assert tool.is_terminal_tool is (name not in non_terminal)


def test_custom_generator_agent_can_declare_goal_solution_terminal() -> None:
    AgentDefinition(
        name="custom_generator",
        description="Custom generator agent.",
        role=AgentRole.GENERATOR,
        terminals=["submit_workflow_handoff"],
        tool_call_limit=10,
    )
    assert has_tool("submit_workflow_handoff")


def test_plan_input_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        PlanTaskInput.model_validate(
            {"id": "a", "agent_name": "executor", "deps": [], "extra": "nope"}
        )
