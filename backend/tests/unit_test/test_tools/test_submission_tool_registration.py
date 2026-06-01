"""Registration and schema checks for Phase 03 submission tools."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents import AgentDefinition
from agents import AgentRole
from tools._framework.factory import ToolFactoryContext, create_tool, has_tool
from tools.submission.planner import PlanTaskInput


PHASE03_TOOLS = (
    "submit_root_outcome",
    "submit_planner_outcome",
    "submit_generator_outcome",
    "submit_reducer_outcome",
    "ask_advisor",
    "submit_advisor_feedback",
    "submit_exploration_result",
)


def test_submission_tools_registered() -> None:
    assert all(has_tool(name) for name in PHASE03_TOOLS)


def test_tool_registry_renamed() -> None:
    """Unified submission names are registered and split terminals are gone."""
    assert has_tool("submit_planner_outcome")
    assert has_tool("submit_root_outcome")
    assert has_tool("submit_generator_outcome")
    assert has_tool("submit_reducer_outcome")
    assert has_tool("delegate_workflow")
    assert has_tool("check_workflow_status")
    assert has_tool("cancel_workflow")
    assert not has_tool("submit_workflow_handoff")
    assert not has_tool("submit_plan_defers_goal")
    assert not has_tool("submit_plan_closes_goal")
    assert not has_tool("submit_generator_success")
    assert not has_tool("submit_generator_failure")
    assert not has_tool("submit_reduction_success")
    assert not has_tool("submit_reduction_failure")
    assert not has_tool("submit_plan_continues_goal")
    assert not has_tool("submit_execution_success")
    assert not has_tool("submit_execution_blocker")
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
        terminals=["submit_generator_outcome"],
        allowed_tools=["delegate_workflow", "check_workflow_status", "cancel_workflow"],
        tool_call_limit=10,
    )
    assert has_tool("submit_generator_outcome")
    assert has_tool("delegate_workflow")


def test_plan_input_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        PlanTaskInput.model_validate(
            {"id": "a", "agent_name": "executor", "deps": [], "extra": "nope"}
        )
