from __future__ import annotations

from agents.registry import get_definition
from team.builtins import (
    DEVELOPER,
    SCOUT,
    TEAM_PLANNER,
    TEAM_REPLANNER,
    VALIDATOR,
    register_all,
)
from tools.core.base import ToolRegistry
from tools.core.factory import ToolkitContext, create_toolkit


def setup_module() -> None:
    register_all()


def test_builtin_team_agents_preload_skills_without_lazy_skill_toolkit() -> None:
    for name in (TEAM_PLANNER, TEAM_REPLANNER, DEVELOPER, VALIDATOR, SCOUT):
        defn = get_definition(name)
        assert defn is not None
        assert defn.include_skills is True
        assert defn.skills, f"{name} should still declare its preloaded playbook"


def test_builtin_team_agents_use_default_tool_call_limits() -> None:
    for name in (TEAM_PLANNER, TEAM_REPLANNER, DEVELOPER, VALIDATOR, SCOUT):
        defn = get_definition(name)
        assert defn is not None
        assert defn.tool_call_limit == 100


def test_team_agents_share_same_code_intelligence_toolkit_surface() -> None:
    planner_ci = create_toolkit(
        "code_intelligence",
        ToolkitContext(metadata={"agent_name": TEAM_PLANNER}),
    )
    developer_ci = create_toolkit(
        "code_intelligence",
        ToolkitContext(metadata={"agent_name": DEVELOPER}),
    )

    assert set(planner_ci.tool_names()) == set(developer_ci.tool_names())


def test_toolkit_instructions_surface_scope_and_search_tools() -> None:
    developer_ci = create_toolkit(
        "code_intelligence",
        ToolkitContext(metadata={"agent_name": DEVELOPER}),
    )
    sandbox_ops = create_toolkit(
        "sandbox_operations",
        ToolkitContext(metadata={"sandbox_id": "sb-test"}),
    )

    assert developer_ci.instructions is not None

    assert sandbox_ops.instructions is not None
    assert "daytona_grep" in sandbox_ops.instructions


def test_team_worker_sandbox_toolkit_includes_codeact() -> None:
    developer_sandbox = create_toolkit(
        "sandbox_operations",
        ToolkitContext(metadata={"agent_name": DEVELOPER, "sandbox_id": "sb-dev"}),
    )
    validator_sandbox = create_toolkit(
        "sandbox_operations",
        ToolkitContext(metadata={"agent_name": VALIDATOR, "sandbox_id": "sb-val"}),
    )

    assert "daytona_codeact" in developer_sandbox.tool_names()
    assert "daytona_codeact" in validator_sandbox.tool_names()
    assert "daytona_edit_file" in developer_sandbox.tool_names()
    # daytona_bash has been removed — all agents use daytona_codeact
    assert "daytona_bash" not in developer_sandbox.tool_names()
    assert "daytona_bash" not in validator_sandbox.tool_names()


def test_task_center_toolkit_survives_restriction() -> None:
    task_center_toolkit = create_toolkit(
        "task_center",
        ToolkitContext(metadata={"agent_name": TEAM_PLANNER}),
    )
    registry = ToolRegistry()
    registry.register_toolkit(task_center_toolkit)
    registry.restrict_to_toolkits(["task_center"])

    assert registry.get_toolkit("task_center") is not None
    assert registry.get("read_task_note") is not None
    # post_note moved to submission toolkit (terminal tools only)
    assert registry.get("task_center_changed_since") is not None
