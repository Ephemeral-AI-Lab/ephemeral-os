"""TerminalToolSelection skill path propagation through terminal routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pytest

from agents import (
    AgentDefinition,
    AgentRole,
    list_definitions,
    register_definition,
    unregister_definition,
)
from task_center._core.terminal_routing import TerminalToolRouter
from task_center.context_engine.scope import ContextScope


@dataclass(frozen=True, slots=True)
class _Deps:
    workflow_store: object = None
    iteration_store: object = None
    attempt_store: object = None
    task_store: object = None


@pytest.fixture(autouse=True)
def _isolate_agent_definitions():
    saved = list_definitions()
    for definition in saved:
        unregister_definition(definition.name)
    yield
    for definition in list_definitions():
        unregister_definition(definition.name)
    for definition in saved:
        register_definition(definition)


def _make_definition(
    *,
    name: str,
    skill: Path | None = None,
    terminals: list[str] | None = None,
    recipe: str = "planner",
) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        description=name,
        role=AgentRole.PLANNER,
        context_recipe=recipe,
        skill=skill,
        terminals=terminals or ["submit_x"],
        tool_call_limit=10,
    )


def test_resolve_returns_skill_path_from_registered_definition(tmp_path: Path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# planner skill")
    base = _make_definition(name="planner_test_base", skill=skill_file)
    register_definition(base)

    selection = TerminalToolRouter().resolve(
        base_agent_name="planner_test_base",
        scope=ContextScope(),
        deps=_Deps(),  # type: ignore[arg-type]
    )

    assert selection.agent_def.skill == skill_file
    assert selection.agent_def.name == "planner_test_base"
    unregister_definition("planner_test_base")


def test_resolve_keeps_base_skill_when_terminals_are_filtered(tmp_path: Path):
    base_skill = tmp_path / "SKILL.md"
    base_skill.write_text("# planner skill")
    base = _make_definition(
        name="planner_test_router",
        skill=base_skill,
        terminals=["submit_plan_closes_goal", "submit_plan_defers_goal"],
    )
    register_definition(base)

    selection = TerminalToolRouter().resolve(
        base_agent_name="planner_test_router",
        scope=ContextScope.for_planner(
            workflow_id=None, iteration_id="i", attempt_id="a"
        ),
        deps=_Deps(),  # type: ignore[arg-type]
    )

    assert selection.agent_def.skill == base_skill
    assert selection.agent_def.name == "planner_test_router"

    unregister_definition("planner_test_router")


def test_resolve_returns_none_when_no_skill_declared():
    plain = _make_definition(name="planner_test_plain", skill=None)
    register_definition(plain)
    selection = TerminalToolRouter().resolve(
        base_agent_name="planner_test_plain",
        scope=ContextScope(),
        deps=_Deps(),  # type: ignore[arg-type]
    )

    assert selection.agent_def.skill is None
    unregister_definition("planner_test_plain")
