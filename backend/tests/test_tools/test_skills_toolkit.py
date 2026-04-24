from __future__ import annotations

from pathlib import Path

import pytest

from engine.runtime.tool_trace import record_tool_trace
from skills.core.registry import SkillRegistry
from skills.core.types import SkillDefinition
from tools.builtins.skills.tools import make_skills_tools
from tools.core.base import ToolExecutionContext


@pytest.mark.asyncio
async def test_load_skill_does_not_append_reference_footer() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="demo-skill",
            description="Demo skill.",
            content="# Demo\n\nUse the main workflow.",
            source="test",
            references={"extra": "Supplementary guidance."},
        )
    )
    tools = {tool.name: tool for tool in make_skills_tools(registry)}
    load_skill = tools.get("load_skill")

    assert load_skill is not None
    result = await load_skill.execute(
        load_skill.input_model(skill_name="demo-skill"),
        ToolExecutionContext(cwd=Path("/tmp")),
    )

    assert result.output == "# Demo\n\nUse the main workflow."
    assert "This skill has" not in result.output
    assert "Use `load_skill_reference` to load any of them." not in result.output


@pytest.mark.asyncio
async def test_load_skill_reference_still_loads_named_references() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="demo-skill",
            description="Demo skill.",
            content="# Demo",
            source="test",
            references={"extra": "Supplementary guidance."},
        )
    )
    tools = {tool.name: tool for tool in make_skills_tools(registry)}
    load_reference = tools.get("load_skill_reference")

    assert load_reference is not None
    result = await load_reference.execute(
        load_reference.input_model(skill_name="demo-skill", reference_name="extra"),
        ToolExecutionContext(cwd=Path("/tmp")),
    )

    assert result.output == "Supplementary guidance."


@pytest.mark.asyncio
async def test_staged_planner_reference_rejects_immediate_load_after_skill() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="team-root-planner-playbook",
            description="Planner skill.",
            content="# Planner",
            source="test",
            references={"synthesize-and-submit": "Synthesis contract."},
        )
    )
    tools = {tool.name: tool for tool in make_skills_tools(registry)}
    load_reference = tools.get("load_skill_reference")
    assert load_reference is not None

    context = ToolExecutionContext(cwd=Path("/tmp"))
    record_tool_trace(
        context.metadata,
        "load_skill",
        {"skill_name": "team-root-planner-playbook"},
    )

    result = await load_reference.execute(
        load_reference.input_model(
            skill_name="team-root-planner-playbook",
            reference_name="synthesize-and-submit",
        ),
        context,
    )

    assert result.is_error is True
    assert "Premature staged planner reference load" in result.output
    assert "Complete the playbook's Analyze/Scout work first" in result.output


@pytest.mark.asyncio
async def test_staged_planner_reference_allows_load_after_intervening_work() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="team-planner-playbook",
            description="Planner skill.",
            content="# Planner",
            source="test",
            references={"submit-child-plan": "Child synthesis contract."},
        )
    )
    tools = {tool.name: tool for tool in make_skills_tools(registry)}
    load_reference = tools.get("load_skill_reference")
    assert load_reference is not None

    context = ToolExecutionContext(cwd=Path("/tmp"))
    record_tool_trace(
        context.metadata,
        "load_skill",
        {"skill_name": "team-planner-playbook"},
    )
    record_tool_trace(
        context.metadata,
        "read_task_details",
        {"task_id": "00000000-0000-0000-0000-000000000001"},
    )

    result = await load_reference.execute(
        load_reference.input_model(
            skill_name="team-planner-playbook",
            reference_name="submit-child-plan",
        ),
        context,
    )

    assert result.is_error is False
    assert result.output == "Child synthesis contract."
