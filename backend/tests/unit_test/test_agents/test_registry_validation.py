"""US-007: agent definition reference validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents import (
    AgentDefinition,
    AgentRole,
    list_definitions,
    register_definition,
    unregister_definition,
    validate_agent_definitions_resolved,
)
from agents.skills import SkillLintError
from workflow.context_engine.engine import AgentDefinitionValidationError


@pytest.fixture(autouse=True)
def _isolate_state():
    saved_definitions = list_definitions()
    _clear_definitions()
    yield
    _clear_definitions()
    for definition in saved_definitions:
        register_definition(definition)


def _clear_definitions() -> None:
    for definition in list_definitions():
        unregister_definition(definition.name)


def test_legacy_variants_field_rejected_by_definition_model():
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="planner",
            description="planner",
            terminals=["submit_x"],
            tool_call_limit=10,
            context_recipe="planner",
            variants=[],
        )
    assert "variants" in str(exc.value)


def test_unknown_context_recipe_is_rejected():
    base = AgentDefinition(
        name="planner",
        description="planner",
        role=AgentRole.PLANNER,
        terminals=["submit_x"],
        tool_call_limit=10,
        context_recipe="not_registered_recipe",
    )
    register_definition(base)

    with pytest.raises(AgentDefinitionValidationError, match="invalid context_recipe"):
        validate_agent_definitions_resolved()


def test_context_recipe_must_match_agent_role():
    base = AgentDefinition(
        name="generator",
        description="generator",
        role=AgentRole.GENERATOR,
        terminals=["submit_x"],
        tool_call_limit=10,
        context_recipe="planner",
    )
    register_definition(base)

    with pytest.raises(AgentDefinitionValidationError, match="cannot build role"):
        validate_agent_definitions_resolved()


def test_clean_setup_passes_validation():
    planner = AgentDefinition(
        name="planner",
        description="planner",
        role=AgentRole.PLANNER,
        context_recipe="planner",
        terminals=["submit_planner_outcome"],
        tool_call_limit=10,
    )
    generator = AgentDefinition(
        name="generator",
        description="generator",
        role=AgentRole.GENERATOR,
        context_recipe="generator",
        terminals=["submit_generator_outcome"],
        tool_call_limit=10,
    )
    for d in (planner, generator):
        register_definition(d)
    # No exception.
    validate_agent_definitions_resolved()


def test_definitions_with_no_recipe_pass_validation():
    """Helper / subagent definitions without context_recipe must not break
    startup — only context-engine-launched agents need a recipe."""
    no_recipe = AgentDefinition(
        name="no_recipe",
        description="no recipe",
        terminals=["submit_x"],
        tool_call_limit=10,
        context_recipe=None,
    )
    register_definition(no_recipe)
    validate_agent_definitions_resolved()


def test_skill_lint_runs_during_resolved_validation(tmp_path):
    skill_file = tmp_path / "planner" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text(
        "---\nname: planner\n---\n\nUse submit_planner_outcome here.",
        encoding="utf-8",
    )
    planner = AgentDefinition(
        name="planner",
        description="planner",
        role=AgentRole.PLANNER,
        terminals=["submit_x"],
        tool_call_limit=10,
        context_recipe="planner",
        skill=skill_file,
    )
    register_definition(planner)

    with pytest.raises(SkillLintError) as exc:
        validate_agent_definitions_resolved()
    assert "submit_planner_outcome" in str(exc.value)
    assert "planner" in str(exc.value)
