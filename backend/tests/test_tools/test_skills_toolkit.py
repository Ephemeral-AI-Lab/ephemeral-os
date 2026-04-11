from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from skills.core.registry import SkillRegistry
from skills.core.types import SkillDefinition
from team.runtime.registry import register as register_team_run
from team.runtime.registry import unregister as unregister_team_run
from tools.builtins.skills import make_skills_toolkit
from tools.core.base import ToolExecutionContext
from tools.core.runtime import ExecutionMetadata, merge_runtime_metadata


def _registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="team-planner-playbook",
            description="planner skill",
            content="planner",
            source="test",
            references={
                "exploration-script": "exploration",
                "scout-launch-contract": "scout-launch",
                "plan-json-contract": "plan-json",
                "task-planning-decomposition": "decomposition",
                "dependency-graph-examples": "dependency-graph",
                "root-plan-self-check": "self-check",
            },
        )
    )
    return registry


def _benchmark_root_context() -> ToolExecutionContext:
    metadata = ExecutionMetadata()
    metadata["agent_name"] = "team_planner"
    metadata["team_run_id"] = "team-run-1"
    metadata["work_item_id"] = "root-1"
    return ToolExecutionContext(cwd=Path.cwd(), metadata=metadata)


async def _execute_reference(
    tool,
    ctx: ToolExecutionContext,
    reference_name: str,
):
    working_ctx = ToolExecutionContext(
        cwd=ctx.cwd,
        metadata=ctx.metadata.with_overrides(tool_id=f"tool-{reference_name}"),
    )
    result = await tool.execute(
        tool.input_model(
            skill_name="team-planner-playbook",
            reference_name=reference_name,
        ),
        working_ctx,
    )
    merge_runtime_metadata(
        original=ctx.metadata,
        updated=working_ctx.metadata,
        result_metadata=result.metadata,
    )
    return result


def _register_benchmark_root_team_run() -> None:
    team_run = SimpleNamespace(
        id="team-run-1",
        root_work_item_id="root-1",
        dispatcher=SimpleNamespace(
            graph={
                "root-1": SimpleNamespace(
                    payload={"fail_to_pass": ["pkg/tests/test_cli.py::test_versions"]}
                )
            }
        ),
    )
    register_team_run(team_run)


@pytest.mark.asyncio
async def test_skills_toolkit_blocks_final_plan_references_before_first_scout_wave():
    _register_benchmark_root_team_run()
    try:
        toolkit = make_skills_toolkit(_registry(), allowed_slugs=["team-planner-playbook"])
        tool = toolkit.get("load_skill_reference")
        assert tool is not None
        ctx = _benchmark_root_context()

        first = await _execute_reference(tool, ctx, "exploration-script")
        second = await _execute_reference(tool, ctx, "scout-launch-contract")
        result = await _execute_reference(tool, ctx, "plan-json-contract")

        assert not first.is_error
        assert not second.is_error
        assert result.is_error
        assert "before the first scout wave" in result.output
        assert "run_subagent(agent_name=\"scout\"" in result.output
    finally:
        unregister_team_run("team-run-1")


@pytest.mark.asyncio
async def test_skills_toolkit_requires_exploration_script_first_on_benchmark_root():
    _register_benchmark_root_team_run()
    try:
        toolkit = make_skills_toolkit(_registry(), allowed_slugs=["team-planner-playbook"])
        tool = toolkit.get("load_skill_reference")
        assert tool is not None

        result = await _execute_reference(tool, _benchmark_root_context(), "scout-launch-contract")

        assert result.is_error
        assert "exploration-script" in result.output
        assert "Load that reference first" in result.output
    finally:
        unregister_team_run("team-run-1")


@pytest.mark.asyncio
async def test_skills_toolkit_requires_decomposition_before_plan_contract():
    _register_benchmark_root_team_run()
    try:
        toolkit = make_skills_toolkit(_registry(), allowed_slugs=["team-planner-playbook"])
        tool = toolkit.get("load_skill_reference")
        assert tool is not None
        ctx = _benchmark_root_context()

        assert not (await _execute_reference(tool, ctx, "exploration-script")).is_error
        assert not (await _execute_reference(tool, ctx, "scout-launch-contract")).is_error
        ctx.metadata["_scout_target_paths_this_turn"] = ["pkg/cli.py"]
        result = await _execute_reference(tool, ctx, "plan-json-contract")

        assert result.is_error
        assert "task-planning-decomposition" in result.output
    finally:
        unregister_team_run("team-run-1")


@pytest.mark.asyncio
async def test_skills_toolkit_blocks_more_references_after_plan_contract():
    _register_benchmark_root_team_run()
    try:
        toolkit = make_skills_toolkit(_registry(), allowed_slugs=["team-planner-playbook"])
        tool = toolkit.get("load_skill_reference")
        assert tool is not None
        ctx = _benchmark_root_context()

        assert not (await _execute_reference(tool, ctx, "exploration-script")).is_error
        assert not (await _execute_reference(tool, ctx, "scout-launch-contract")).is_error
        ctx.metadata["_scout_target_paths_this_turn"] = ["pkg/cli.py"]
        assert not (await _execute_reference(tool, ctx, "task-planning-decomposition")).is_error
        assert not (await _execute_reference(tool, ctx, "plan-json-contract")).is_error
        result = await _execute_reference(tool, ctx, "root-plan-self-check")

        assert result.is_error
        assert "stop loading further" in result.output
    finally:
        unregister_team_run("team-run-1")


@pytest.mark.asyncio
async def test_skills_toolkit_allows_final_plan_reference_after_scout_wave():
    _register_benchmark_root_team_run()
    try:
        toolkit = make_skills_toolkit(_registry(), allowed_slugs=["team-planner-playbook"])
        tool = toolkit.get("load_skill_reference")
        assert tool is not None
        ctx = _benchmark_root_context()

        assert not (await _execute_reference(tool, ctx, "exploration-script")).is_error
        assert not (await _execute_reference(tool, ctx, "scout-launch-contract")).is_error
        ctx.metadata["_scout_target_paths_this_turn"] = ["pkg/cli.py"]
        assert not (await _execute_reference(tool, ctx, "task-planning-decomposition")).is_error
        result = await _execute_reference(tool, ctx, "plan-json-contract")

        assert not result.is_error
        assert result.output == "plan-json"
    finally:
        unregister_team_run("team-run-1")
