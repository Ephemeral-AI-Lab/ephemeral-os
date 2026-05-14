"""ContextEngine + ContextComposer + engine exceptions.

This module is the single launch entry point for every agent spawn. The
composer threads ``base_agent_name`` + :class:`ContextScope` through the
resolver, engine, and renderer in a fixed order:

    resolver.resolve → engine.build → packet.blocks.extend(...) →
    context_packet_store.insert → renderer.render → :class:`LaunchBundle`

That is the entire surface. Adding a new role means registering a recipe
and (optionally) declaring variants on its ``agent.md`` — no engine code
changes.

The engine owns no role names. Every recipe is registered against a string
id and looked up at call time. Recipes receive :class:`ContextScope` and a
shared :class:`ContextEngineDeps` bundle.

Engine exceptions are colocated here so every other engine file imports
from one place and reverse-imports stay simple.

**Import-order note:** Exception classes are defined before the package
imports of ``scope``, ``recipes_registry``, and ``agent_routing`` because
those modules import the exception names back from this module. Defining
the symbols first keeps the partial-import view consistent during cycle
resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol


# ---- Exceptions ---------------------------------------------------------
#
# Defined first so that ``scope``, ``recipes_registry``, and
# ``agent_routing`` (imported below or downstream) can
# ``from task_center.context_engine.core import …`` the error names while
# this module is still partially loaded.


class ContextEngineError(Exception):
    """Generic context engine failure (e.g. missing prior-episode fields)."""


class RecipeScopeError(ContextEngineError):
    """A recipe was called with a :class:`ContextScope` missing required fields."""


class MissingContextRecipeError(ContextEngineError):
    """An agent definition was selected for composition but has no
    ``context_recipe`` declared in frontmatter."""


class AgentDefinitionValidationError(ContextEngineError):
    """A registered :class:`AgentDefinition` references unknown or invalid
    variants / predicates / context recipes — caught at startup."""


# ---- Imports (deferred past the exception block) ------------------------

from agents import AgentDefinition  # noqa: E402
from task_center.context_engine.packet import ContextPacket  # noqa: E402
from task_center.context_engine.recipes_registry import RecipeRegistry  # noqa: E402
from task_center.context_engine.renderer import MarkdownPromptRenderer  # noqa: E402
from task_center.context_engine.scope import ContextScope  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import MissionStoreProtocol
    from task_center._core.persistence import AttemptStoreProtocol
    from task_center._core.persistence import TaskStoreProtocol
    from task_center._core.persistence import EpisodeStoreProtocol
    from task_center._core.agent_routing import RuleBasedAgentResolver


# ---- Engine -------------------------------------------------------------


class ContextPacketStoreProtocol(Protocol):
    def insert(self, packet: ContextPacket) -> str: ...

    def get(self, context_packet_id: str) -> ContextPacket | None: ...


@dataclass(frozen=True, slots=True)
class ContextEngineDeps:
    """Frozen bundle of stores recipes may read from.

    The bundle is intentionally narrow: recipes never reach for globals or
    runtime objects, so swapping a store in tests is one keyword argument.
    """

    mission_store: MissionStoreProtocol
    episode_store: EpisodeStoreProtocol
    attempt_store: AttemptStoreProtocol
    task_store: TaskStoreProtocol

    # Optional: when supplied, the composer persists rendered packet inputs.
    context_packet_store: ContextPacketStoreProtocol | None = None


class ContextEngine:
    """Routes recipe ids to registered builders."""

    def __init__(
        self,
        deps: ContextEngineDeps,
    ) -> None:
        self._deps = deps

    @property
    def deps(self) -> ContextEngineDeps:
        return self._deps

    def build(self, recipe_id: str, scope: ContextScope) -> ContextPacket:
        recipe = RecipeRegistry.get(recipe_id)
        scope.assert_fields(recipe.required_scope_fields)
        return recipe.build(scope, self._deps)


# ---- Composer -----------------------------------------------------------
#
# ``RuleBasedAgentResolver`` is imported lazily inside ``ContextComposer.default``
# because ``agent_routing`` imports ``ContextEngineDeps`` from this module;
# importing the resolver at module top forms a cycle when an external entry
# point loads ``agent_routing`` before ``core``. Deferring the resolver
# import keeps both load orders safe.


@dataclass(frozen=True, slots=True)
class LaunchBundle:
    """The composer's output: everything the launcher needs."""

    agent_def: AgentDefinition
    rendered_prompt: str
    packet: ContextPacket
    context_packet_id: str | None


@dataclass(frozen=True, slots=True)
class ContextComposer:
    """Single launch entry point. Frozen so dependencies are explicit."""

    resolver: RuleBasedAgentResolver
    engine: ContextEngine
    renderer: MarkdownPromptRenderer

    @classmethod
    def default(
        cls,
        engine: ContextEngine,
    ) -> ContextComposer:
        from task_center._core.agent_routing import RuleBasedAgentResolver

        return cls(
            resolver=RuleBasedAgentResolver(),
            engine=engine,
            renderer=MarkdownPromptRenderer(),
        )

    def compose(
        self, *, base_agent_name: str, scope: ContextScope
    ) -> LaunchBundle:
        selection = self.resolver.resolve(
            base_agent_name=base_agent_name,
            scope=scope,
            deps=self.engine.deps,
        )
        # ``resolver.resolve`` enforces context_recipe presence and raises
        # ``MissingContextRecipeError`` for both base and variant-target paths.
        packet = self.engine.build(selection.context_recipe, scope)
        if selection.required_context_blocks:
            packet.blocks.extend(selection.required_context_blocks)
        context_packet_id = self._persist(packet)
        rendered_prompt = self.renderer.render(packet)
        return LaunchBundle(
            agent_def=selection.agent_def,
            rendered_prompt=rendered_prompt,
            packet=packet,
            context_packet_id=context_packet_id,
        )

    # ---- internals ------------------------------------------------------

    def _persist(self, packet: ContextPacket) -> str | None:
        store = self.engine.deps.context_packet_store
        if store is None:
            return None
        return store.insert(packet)
