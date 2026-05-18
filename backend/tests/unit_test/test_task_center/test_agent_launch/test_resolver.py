"""US-006: PredicateRegistry + RuleBasedAgentResolver behavior."""

from __future__ import annotations

import pytest

from agents import (
    AgentDefinition,
    AgentVariant,
    list_definitions,
    register_definition,
    unregister_definition,
)
from task_center.context_engine.core import (
    ContextEngineDeps,
    AgentDefinitionValidationError,
    MissingContextRecipeError,
)
from task_center._core.agent_routing import (
    AgentSelection,
    PredicateRegistry,
    ResolverContext,
    RuleBasedAgentResolver,
)
from task_center.context_engine.scope import ContextScope


@pytest.fixture(autouse=True)
def _isolate_registries():
    saved_predicates = dict(PredicateRegistry._registry)
    saved_definitions = list_definitions()
    PredicateRegistry.clear()
    _clear_definitions()
    yield
    PredicateRegistry.clear()
    _clear_definitions()
    PredicateRegistry._registry.update(saved_predicates)
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
        goal_store=_S(), iteration_store=_S(),  # type: ignore[arg-type]
        attempt_store=_S(), task_store=_S(),  # type: ignore[arg-type]
    )


@pytest.fixture
def planner_with_variant():
    base = AgentDefinition(
        name="planner",
        description="planner",
        context_recipe="planner",
        terminals=["submit_plan_closes_goal", "submit_plan_defers_goal"],
        variants=[
            AgentVariant(
                when="needs_full_only",
                use="planner_full_only",
                note="ancestry has partial-plan caller",
            )
        ],
    )
    full_only = AgentDefinition(
        name="planner_full_only",
        description="planner",
        context_recipe="planner",
        terminals=["submit_plan_closes_goal"],
    )
    register_definition(base)
    register_definition(full_only)
    return base, full_only


def test_empty_variants_returns_base_fast_path(deps):
    base = AgentDefinition(
        name="generator",
        description="g",
        context_recipe="generator",
    )
    register_definition(base)
    sel = RuleBasedAgentResolver().resolve(
        base_agent_name="generator",
        scope=ContextScope(goal_id="r"),
        deps=deps,
    )
    assert isinstance(sel, AgentSelection)
    assert sel.agent_def.name == "generator"
    assert sel.context_recipe == "generator"
    # Post-v3.3: ``required_context_blocks`` is no longer on AgentSelection.
    # Variant-declared blocks (if any) are silently ignored — the
    # ``required_context_blocks`` field on ``AgentVariant`` is deprecated
    # and slated for removal in follow-up #5.
    assert not hasattr(sel, "required_context_blocks")


@pytest.mark.usefixtures("planner_with_variant")
def test_variant_predicate_match_picks_target(deps):
    PredicateRegistry.register("needs_full_only", lambda ctx: True)
    sel = RuleBasedAgentResolver().resolve(
        base_agent_name="planner",
        scope=ContextScope(goal_id="r"),
        deps=deps,
    )
    assert sel.agent_def.name == "planner_full_only"
    assert "submit_plan_defers_goal" not in sel.agent_def.terminals
    assert sel.reason == "ancestry has partial-plan caller"


@pytest.mark.usefixtures("planner_with_variant")
def test_predicate_no_match_falls_back_to_base(deps):
    PredicateRegistry.register("needs_full_only", lambda ctx: False)
    sel = RuleBasedAgentResolver().resolve(
        base_agent_name="planner",
        scope=ContextScope(goal_id="r"),
        deps=deps,
    )
    assert sel.agent_def.name == "planner"


def test_declared_order_priority(deps):
    PredicateRegistry.register("first", lambda ctx: False)
    PredicateRegistry.register("second", lambda ctx: True)
    PredicateRegistry.register("third", lambda ctx: True)
    base = AgentDefinition(
        name="x",
        description="x",
        context_recipe="x_v1",
        variants=[
            AgentVariant(when="first", use="alt_a"),
            AgentVariant(when="second", use="alt_b"),
            AgentVariant(when="third", use="alt_c"),
        ],
    )
    alt_b = AgentDefinition(name="alt_b", description="b", context_recipe="x_v1")
    alt_c = AgentDefinition(name="alt_c", description="c", context_recipe="x_v1")
    alt_a = AgentDefinition(name="alt_a", description="a", context_recipe="x_v1")
    for d in (base, alt_a, alt_b, alt_c):
        register_definition(d)
    sel = RuleBasedAgentResolver().resolve(
        base_agent_name="x", scope=ContextScope(goal_id="r"), deps=deps
    )
    assert sel.agent_def.name == "alt_b", "first matching variant wins"


def test_nested_variant_target_rejected(deps):
    PredicateRegistry.register("always", lambda ctx: True)
    base = AgentDefinition(
        name="base",
        description="base",
        context_recipe="x_v1",
        variants=[AgentVariant(when="always", use="middle")],
    )
    middle = AgentDefinition(
        name="middle",
        description="middle",
        context_recipe="x_v1",
        variants=[AgentVariant(when="always", use="leaf")],
    )
    leaf = AgentDefinition(name="leaf", description="leaf", context_recipe="x_v1")
    for d in (base, middle, leaf):
        register_definition(d)
    with pytest.raises(AgentDefinitionValidationError):
        RuleBasedAgentResolver().resolve(
            base_agent_name="base",
            scope=ContextScope(goal_id="r"),
            deps=deps,
        )


@pytest.mark.usefixtures("planner_with_variant")
def test_predicate_exception_propagates_no_fail_open(deps):
    def _boom(ctx: ResolverContext) -> bool:
        raise RuntimeError("predicate exploded")

    PredicateRegistry.register("needs_full_only", _boom)
    with pytest.raises(RuntimeError):
        RuleBasedAgentResolver().resolve(
            base_agent_name="planner",
            scope=ContextScope(goal_id="r"),
            deps=deps,
        )


def test_missing_context_recipe_raises(deps):
    base = AgentDefinition(name="bare", description="bare")
    register_definition(base)
    with pytest.raises(MissingContextRecipeError):
        RuleBasedAgentResolver().resolve(
            base_agent_name="bare",
            scope=ContextScope(goal_id="r"),
            deps=deps,
        )
