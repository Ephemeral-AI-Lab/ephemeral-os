"""US-012: ContextComposer single-method orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from agents import (
    AgentDefinition,
    AgentSelectionBlock,
    AgentVariant,
    list_definitions,
    register_definition,
    unregister_definition,
)
from db.base import Base
from db.stores.context_packet_store import ContextPacketStore
from task_center.context_engine.core import (
    ContextComposer,
    ContextEngine,
    ContextEngineDeps,
    LaunchBundle,
    MissingContextRecipeError,
)
from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center._core.agent_routing import PredicateRegistry, RuleBasedAgentResolver
from task_center.context_engine.recipes_registry import (
    ContextRecipe,
    RecipeRegistry,
)
from task_center.context_engine.scope import ContextScope


@pytest.fixture(autouse=True)
def _isolate():
    saved_predicates = dict(PredicateRegistry._registry)
    saved_recipes = dict(RecipeRegistry._registry)
    saved_definitions = list_definitions()
    PredicateRegistry.clear()
    RecipeRegistry.clear()
    _clear_definitions()
    yield
    PredicateRegistry.clear()
    RecipeRegistry.clear()
    _clear_definitions()
    PredicateRegistry._registry.update(saved_predicates)
    RecipeRegistry._registry.update(saved_recipes)
    for definition in saved_definitions:
        register_definition(definition)


def _clear_definitions() -> None:
    for definition in list_definitions():
        unregister_definition(definition.name)


@pytest.fixture
def packet_store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = ContextPacketStore()
    store.initialize(sf)
    yield store
    engine.dispose()


def _ok_recipe(recipe_id: str):
    def _build(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
        return ContextPacket(
            target_role="planner",
            target_id=scope.attempt_id,
            canonical_refs=ContextRefs(
                goal_id=scope.goal_id,
                iteration_id=scope.iteration_id,
                attempt_id=scope.attempt_id,
            ),
            blocks=[
                ContextBlock(
                    kind="iteration_statement",
                    priority=ContextPriority.REQUIRED,
                    text="goal",
                )
            ],
        )

    return ContextRecipe(
        id=recipe_id,
        required_scope_fields=frozenset(
            {"goal_id", "iteration_id", "attempt_id"}
        ),
        build=_build,
    )


def _stub_deps(packet_store) -> ContextEngineDeps:
    class _S:
        def get(self, *_args, **_kwargs):
            return None

    return ContextEngineDeps(
        goal_store=_S(),  # type: ignore[arg-type]
        iteration_store=_S(),  # type: ignore[arg-type]
        attempt_store=_S(),  # type: ignore[arg-type]
        task_store=_S(),  # type: ignore[arg-type]
        context_packet_store=packet_store,
    )


def test_compose_threads_calls_in_order(packet_store):
    RecipeRegistry.register(_ok_recipe("planner"))
    base = AgentDefinition(
        name="planner",
        description="planner",
        context_recipe="planner",
        system_prompt="SYSTEM PROMPT",
    )
    register_definition(base)
    deps = _stub_deps(packet_store)
    composer = ContextComposer.default(ContextEngine(deps))
    bundle = composer.compose(
        base_agent_name="planner",
        scope=ContextScope(
            goal_id="r", iteration_id="s", attempt_id="g"
        ),
    )
    assert isinstance(bundle, LaunchBundle)
    assert bundle.agent_def.name == "planner"
    assert bundle.agent_def.system_prompt == "SYSTEM PROMPT"
    assert bundle.context_packet_id is not None
    assert "<current_iteration>\ngoal\n</current_iteration>" in bundle.context_message
    # Packet was persisted.
    assert packet_store.get(bundle.context_packet_id) is not None


def test_required_context_blocks_appended_before_render(packet_store):
    PredicateRegistry.register("always", lambda ctx: True)
    RecipeRegistry.register(_ok_recipe("planner"))
    base = AgentDefinition(
        name="planner",
        description="planner",
        context_recipe="planner",
        variants=[
            AgentVariant(
                when="always",
                use="planner_full_only",
                required_context_blocks=[
                    AgentSelectionBlock(
                        kind="launch_notice",
                        priority="required",
                        text="variant selected.",
                        metadata={"tag": "launch_notice"},
                    )
                ],
            )
        ],
    )
    full_only = AgentDefinition(
        name="planner_full_only",
        description="planner",
        context_recipe="planner",
        system_prompt="FULL ONLY",
    )
    register_definition(base)
    register_definition(full_only)

    deps = _stub_deps(packet_store)
    composer = ContextComposer.default(ContextEngine(deps))
    bundle = composer.compose(
        base_agent_name="planner",
        scope=ContextScope(
            goal_id="r", iteration_id="s", attempt_id="g"
        ),
    )
    assert bundle.agent_def.name == "planner_full_only"
    kinds = [b.kind for b in bundle.packet.blocks]
    assert "launch_notice" in kinds
    assert "variant selected." in bundle.context_message


def test_compose_persists_packet_only_with_store():
    """When deps.context_packet_store is None, composer skips persistence."""
    RecipeRegistry.register(_ok_recipe("planner"))
    base = AgentDefinition(
        name="planner",
        description="planner",
        context_recipe="planner",
    )
    register_definition(base)

    class _S:
        def get(self, *_args, **_kwargs):
            return None

    deps = ContextEngineDeps(
        goal_store=_S(),  # type: ignore[arg-type]
        iteration_store=_S(),  # type: ignore[arg-type]
        attempt_store=_S(),  # type: ignore[arg-type]
        task_store=_S(),  # type: ignore[arg-type]
        context_packet_store=None,
    )
    composer = ContextComposer.default(ContextEngine(deps))
    bundle = composer.compose(
        base_agent_name="planner",
        scope=ContextScope(
            goal_id="r", iteration_id="s", attempt_id="g"
        ),
    )
    assert bundle.context_packet_id is None


def test_resolver_engine_renderer_called_with_correct_args(packet_store):
    """Mock resolver/engine/renderer and assert the wiring contract."""
    RecipeRegistry.register(_ok_recipe("planner"))
    base = AgentDefinition(
        name="planner",
        description="planner",
        context_recipe="planner",
        system_prompt="P",
    )
    register_definition(base)

    deps = _stub_deps(packet_store)
    engine = ContextEngine(deps)
    renderer = MagicMock()
    renderer.render_context.return_value = "RENDERED"
    renderer.render_role_instruction.return_value = "ROLE INSTRUCTION"
    composer = ContextComposer(
        resolver=RuleBasedAgentResolver(),
        engine=engine,
        renderer=renderer,
    )

    scope = ContextScope(
        goal_id="r", iteration_id="s", attempt_id="g"
    )
    bundle = composer.compose(base_agent_name="planner", scope=scope)
    renderer.render_context.assert_called_once()
    rendered_packet = renderer.render_context.call_args[0][0]
    assert isinstance(rendered_packet, ContextPacket)
    renderer.render_role_instruction.assert_called_once()
    assert bundle.context_message == "RENDERED"
    assert bundle.role_instruction_message == "ROLE INSTRUCTION"


def _ok_recipe_with_role_instruction(recipe_id: str):
    """Recipe that emits a role_instruction block so compose() has something to
    extend with the terminal-tool catalog (acceptance criterion §6 #8).
    """

    def _build(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
        return ContextPacket(
            target_role="planner",
            target_id=scope.attempt_id,
            canonical_refs=ContextRefs(
                goal_id=scope.goal_id,
                iteration_id=scope.iteration_id,
                attempt_id=scope.attempt_id,
            ),
            blocks=[
                ContextBlock(
                    kind="iteration_statement",
                    priority=ContextPriority.REQUIRED,
                    text="goal",
                ),
                ContextBlock(
                    kind="role_instruction",
                    priority=ContextPriority.REQUIRED,
                    text="HOW TO PROCEED",
                ),
            ],
        )

    return ContextRecipe(
        id=recipe_id,
        required_scope_fields=frozenset(
            {"goal_id", "iteration_id", "attempt_id"}
        ),
        build=_build,
    )


def test_compose_appends_terminal_catalog_to_role_instruction(packet_store):
    """§6 #8: main-agent user_msg_2 must list every terminal with selection_guidance."""
    from tools._terminals.registry import TERMINAL_DESCRIPTORS

    RecipeRegistry.register(_ok_recipe_with_role_instruction("planner"))
    register_definition(
        AgentDefinition(
            name="planner",
            description="planner",
            context_recipe="planner",
            terminals=["submit_plan_closes_goal", "submit_plan_continues_goal"],
        )
    )
    deps = _stub_deps(packet_store)
    composer = ContextComposer.default(ContextEngine(deps))
    bundle = composer.compose(
        base_agent_name="planner",
        scope=ContextScope(
            goal_id="r", iteration_id="s", attempt_id="g"
        ),
    )

    role_msg = bundle.role_instruction_message
    assert role_msg is not None
    # Original role_instruction text is preserved.
    assert "HOW TO PROCEED" in role_msg
    # Catalog heading + each parent-facing selection_guidance from the
    # registry shows up in user_msg_2.
    assert "# Terminal tools you may call" in role_msg
    for terminal in ("submit_plan_closes_goal", "submit_plan_continues_goal"):
        assert terminal in role_msg
        guidance = TERMINAL_DESCRIPTORS[terminal].selection_guidance
        # First substantive fragment of the guidance must appear verbatim.
        assert guidance[:30] in role_msg
    # The closing instruction reinforces the advisor-loop discipline.
    assert "ask_advisor" in role_msg


def test_compose_skips_catalog_when_agent_has_no_terminals(packet_store):
    """A profile that declares no terminals leaves role_instruction unchanged."""
    RecipeRegistry.register(_ok_recipe_with_role_instruction("planner"))
    register_definition(
        AgentDefinition(
            name="planner",
            description="planner",
            context_recipe="planner",
            terminals=[],
        )
    )
    deps = _stub_deps(packet_store)
    composer = ContextComposer.default(ContextEngine(deps))
    bundle = composer.compose(
        base_agent_name="planner",
        scope=ContextScope(
            goal_id="r", iteration_id="s", attempt_id="g"
        ),
    )

    role_msg = bundle.role_instruction_message
    assert role_msg is not None
    assert "HOW TO PROCEED" in role_msg
    assert "# Terminal tools you may call" not in role_msg


def test_missing_context_recipe_raises_before_render(packet_store):
    base = AgentDefinition(name="bare", description="bare")
    register_definition(base)
    deps = _stub_deps(packet_store)
    composer = ContextComposer.default(ContextEngine(deps))
    with pytest.raises(MissingContextRecipeError):
        composer.compose(
            base_agent_name="bare",
            scope=ContextScope(goal_id="r"),
        )
