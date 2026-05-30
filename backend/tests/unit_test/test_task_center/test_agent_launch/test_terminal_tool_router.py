"""TerminalToolRouter behavior.

The router no longer owns per-role routing logic — that lives in each profile's
``terminal_routing`` module (tested in ``test_agents``). These tests cover the
router's mechanics: dispatch to the attached router, intersection with declared
terminals, the effective-copy (no-mutation) contract, and recipe enforcement.
"""

from __future__ import annotations

import pytest

from agents import (
    AgentDefinition,
    list_definitions,
    register_definition,
    unregister_definition,
)
from task_center._core.terminal_routing import (
    TerminalToolRouter,
    TerminalToolSelection,
)
from task_center.context_engine.engine import ContextEngineDeps, MissingContextRecipeError
from task_center.context_engine.scope import ContextScope


@pytest.fixture(autouse=True)
def _isolate_registries():
    saved_definitions = list_definitions()
    _clear_definitions()
    yield
    _clear_definitions()
    for definition in saved_definitions:
        register_definition(definition)


def _clear_definitions() -> None:
    for definition in list_definitions():
        unregister_definition(definition.name)


@pytest.fixture
def deps() -> ContextEngineDeps:
    class _S:
        def get(self, *_args, **_kwargs):
            return None

    return ContextEngineDeps(
        workflow_store=_S(),  # type: ignore[arg-type]
        iteration_store=_S(),  # type: ignore[arg-type]
        attempt_store=_S(),  # type: ignore[arg-type]
        task_store=_S(),  # type: ignore[arg-type]
    )


def _register(
    *,
    name: str,
    terminals: list[str],
    recipe: str = "recipe",
    routing=None,
) -> AgentDefinition:
    definition = AgentDefinition(
        name=name,
        description=name,
        context_recipe=recipe,
        terminals=terminals,
        tool_call_limit=10,
    )
    if routing is not None:
        definition._terminal_router = routing
    register_definition(definition)
    return definition


def test_router_intersects_and_returns_effective_copy_without_mutating(deps, monkeypatch):
    original = _register(
        name="planner",
        terminals=["submit_plan_closes_goal", "submit_plan_defers_goal"],
        recipe="planner",
        routing=lambda *, is_nested, has_workflow: frozenset({"submit_plan_closes_goal"}),
    )
    monkeypatch.setattr(
        "task_center._core.terminal_routing._nested_workflow_depth_gt_1",
        lambda ctx: True,
    )

    selection = TerminalToolRouter().resolve(
        base_agent_name="planner",
        scope=ContextScope(workflow_id="g"),
        deps=deps,
    )

    assert isinstance(selection, TerminalToolSelection)
    assert selection.agent_def.terminals == ["submit_plan_closes_goal"]
    # Registered definition is untouched (effective copy).
    assert original.terminals == ["submit_plan_closes_goal", "submit_plan_defers_goal"]


def test_router_passes_depth_and_workflow_flags(deps, monkeypatch):
    seen: dict[str, bool] = {}

    def _spy(*, is_nested: bool, has_workflow: bool) -> frozenset[str] | None:
        seen["is_nested"] = is_nested
        seen["has_workflow"] = has_workflow
        return None

    _register(name="planner", terminals=["submit_plan_closes_goal"], recipe="planner", routing=_spy)
    monkeypatch.setattr(
        "task_center._core.terminal_routing._nested_workflow_depth_gt_1",
        lambda ctx: True,
    )

    TerminalToolRouter().resolve(
        base_agent_name="planner",
        scope=ContextScope(workflow_id="g"),
        deps=deps,
    )

    assert seen == {"is_nested": True, "has_workflow": True}


def test_no_routing_module_keeps_all_terminals(deps, monkeypatch):
    _register(
        name="reducer",
        terminals=["submit_reduction_success", "submit_reduction_failure"],
        recipe="reducer",
        routing=None,
    )
    monkeypatch.setattr(
        "task_center._core.terminal_routing._nested_workflow_depth_gt_1",
        lambda ctx: True,
    )

    selection = TerminalToolRouter().resolve(
        base_agent_name="reducer",
        scope=ContextScope(workflow_id="g"),
        deps=deps,
    )

    assert selection.agent_def.terminals == [
        "submit_reduction_success",
        "submit_reduction_failure",
    ]


def test_router_none_result_keeps_all_terminals(deps, monkeypatch):
    _register(
        name="standalone_executor",
        terminals=["submit_execution_success"],
        recipe="generator",
        routing=lambda *, is_nested, has_workflow: None,
    )
    monkeypatch.setattr(
        "task_center._core.terminal_routing._nested_workflow_depth_gt_1",
        lambda ctx: True,
    )

    selection = TerminalToolRouter().resolve(
        base_agent_name="standalone_executor",
        scope=ContextScope(workflow_id=None),
        deps=deps,
    )

    assert selection.agent_def.terminals == ["submit_execution_success"]


def test_missing_context_recipe_raises(deps):
    register_definition(
        AgentDefinition(
            name="bare",
            description="bare",
            terminals=["submit_x"],
            tool_call_limit=10,
        )
    )
    with pytest.raises(MissingContextRecipeError):
        TerminalToolRouter().resolve(
            base_agent_name="bare",
            scope=ContextScope(workflow_id="g"),
            deps=deps,
        )
