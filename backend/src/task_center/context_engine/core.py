"""Engine + composer.

``ContextComposer`` threads ``base_agent_name`` + :class:`ContextScope` through
the resolver, engine, and renderer to produce a :class:`LaunchBundle`. Recipe
ids are looked up at call time; adding a role means registering a recipe (and
optionally declaring variants on its ``agent.md``) — no engine code changes.

Exceptions are re-exported from :mod:`.exceptions` so existing callers that
``from task_center.context_engine.core import ContextEngineError`` keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agents import AgentDefinition

from task_center.context_engine.exceptions import (
    AgentDefinitionValidationError,
    ContextEngineError,
    MissingContextRecipeError,
    RecipeScopeError,
)
from task_center.context_engine.packet import ContextPacket
from task_center.context_engine.recipes_registry import RecipeRegistry
from task_center.context_engine.renderer import MarkdownPromptRenderer
from task_center.context_engine.scope import ContextScope

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.agent_routing import RuleBasedAgentResolver
    from task_center._core.persistence import (
        AttemptStoreProtocol,
        IterationStoreProtocol,
        GoalStoreProtocol,
        TaskStoreProtocol,
    )

__all__ = [
    "AgentDefinitionValidationError",
    "ContextComposer",
    "ContextEngine",
    "ContextEngineDeps",
    "ContextEngineError",
    "ContextPacketStoreProtocol",
    "LaunchBundle",
    "MissingContextRecipeError",
    "RecipeScopeError",
]


class ContextPacketStoreProtocol(Protocol):
    def insert(self, packet: ContextPacket) -> str: ...

    def get(self, context_packet_id: str) -> ContextPacket | None: ...


@dataclass(frozen=True, slots=True)
class ContextEngineDeps:
    """Frozen bundle of stores recipes may read from.

    Recipes never reach for globals or runtime objects, so swapping a store in
    tests is one keyword argument.
    """

    goal_store: GoalStoreProtocol
    iteration_store: IterationStoreProtocol
    attempt_store: AttemptStoreProtocol
    task_store: TaskStoreProtocol

    # Optional: when supplied, the composer persists rendered packet inputs.
    context_packet_store: ContextPacketStoreProtocol | None = None


@dataclass(frozen=True, slots=True)
class ContextEngine:
    """Routes recipe ids to registered builders."""

    deps: ContextEngineDeps

    def build(self, recipe_id: str, scope: ContextScope) -> ContextPacket:
        recipe = RecipeRegistry.get(recipe_id)
        scope.assert_fields(recipe.required_scope_fields)
        return recipe.build(scope, self.deps)


@dataclass(frozen=True, slots=True)
class LaunchBundle:
    """The composer's output: everything the launcher needs.

    The launch is split into two user messages:

    * ``context_message`` — rendered world state, no role_instruction inline.
    * ``role_instruction_message`` — per-call ask; ``None`` for agents (e.g.
      entry_executor) whose recipe emits no role_instruction block, signalling
      the launcher to fall back to a single user-message launch.
    """

    agent_def: AgentDefinition
    context_message: str
    role_instruction_message: str | None
    packet: ContextPacket
    context_packet_id: str | None


@dataclass(frozen=True, slots=True)
class ContextComposer:
    """Single launch entry point. Frozen so dependencies are explicit."""

    resolver: RuleBasedAgentResolver
    engine: ContextEngine
    renderer: MarkdownPromptRenderer

    @classmethod
    def default(cls, engine: ContextEngine) -> ContextComposer:
        # Lazy import: _core.agent_routing imports ContextEngineDeps from here.
        from task_center._core.agent_routing import RuleBasedAgentResolver

        return cls(
            resolver=RuleBasedAgentResolver(),
            engine=engine,
            renderer=MarkdownPromptRenderer(),
        )

    def compose(
        self, *, base_agent_name: str, scope: ContextScope
    ) -> LaunchBundle:
        # ``resolver.resolve`` enforces context_recipe presence and raises
        # ``MissingContextRecipeError`` for both base and variant-target paths.
        selection = self.resolver.resolve(
            base_agent_name=base_agent_name,
            scope=scope,
            deps=self.engine.deps,
        )
        packet = self.engine.build(selection.context_recipe, scope)
        if selection.required_context_blocks:
            packet.blocks.extend(selection.required_context_blocks)
        store = self.engine.deps.context_packet_store
        context_packet_id = store.insert(packet) if store is not None else None
        return LaunchBundle(
            agent_def=selection.agent_def,
            context_message=self.renderer.render_context(packet),
            role_instruction_message=self.renderer.render_role_instruction(packet),
            packet=packet,
            context_packet_id=context_packet_id,
        )
