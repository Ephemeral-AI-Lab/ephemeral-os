"""Named predicates referenced by ``agent.md`` ``variants:`` entries.

Predicates are pure named functions registered in code. ``agent.md`` only
references them by id — there is no eval/dsl in the markdown.

The depth-based predicates delegate to
:func:`task_center.mission.ancestry.nested_mission_depth` and gate on the
``MAX_HANDOFF_DEPTH`` module constant so the threshold is the single source of
truth for variant routing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from task_center.context_engine.engine import ContextEngineDeps
from task_center.context_engine.scope import ContextScope
from task_center.mission.ancestry import nested_mission_depth


# Maximum nested-mission depth at which an executor profile still offers a
# handoff terminal. Above this, the leaf executor profile is selected (success
# + failure terminals only). Range-named predicates encode the threshold so
# renaming this constant does not require touching any frontmatter.
MAX_HANDOFF_DEPTH = 2


@dataclass(frozen=True, slots=True)
class ResolverContext:
    """Identity + dependency bundle handed to every predicate."""

    scope: ContextScope
    deps: ContextEngineDeps


PredicateFn = Callable[[ResolverContext], bool]


class PredicateRegistry:
    """Process-global registry. Tests use ``clear`` to start fresh."""

    _registry: ClassVar[dict[str, PredicateFn]] = {}

    @classmethod
    def register(cls, name: str, fn: PredicateFn) -> None:
        cls._registry[name] = fn

    @classmethod
    def get(cls, name: str) -> PredicateFn:
        try:
            return cls._registry[name]
        except KeyError as exc:
            raise KeyError(
                f"Predicate {name!r} is not registered. "
                f"Known predicates: {sorted(cls._registry)!r}"
            ) from exc

    @classmethod
    def has(cls, name: str) -> bool:
        return name in cls._registry

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()


def _depth(ctx: ResolverContext) -> int:
    """Return the nested-mission depth for ``ctx``.

    Scopes without a mission (e.g. the top-level entry executor) have no
    caller-attempt ancestry by construction, so depth is zero.
    """
    mission_id = ctx.scope.mission_id
    if mission_id is None:
        return 0
    return nested_mission_depth(
        mission_id=mission_id,
        mission_store=ctx.deps.mission_store,
        episode_store=ctx.deps.episode_store,
        attempt_store=ctx.deps.attempt_store,
        task_store=ctx.deps.task_store,
    )


def _nested_mission_depth_within_handoff_range(ctx: ResolverContext) -> bool:
    """True when depth ≤ MAX_HANDOFF_DEPTH (executor may still hand off)."""
    return _depth(ctx) <= MAX_HANDOFF_DEPTH


def _nested_mission_depth_above_handoff_range(ctx: ResolverContext) -> bool:
    """True when depth > MAX_HANDOFF_DEPTH (leaf executor, no further handoff)."""
    return _depth(ctx) > MAX_HANDOFF_DEPTH


def _nested_mission_depth_gt_1(ctx: ResolverContext) -> bool:
    """True when depth > 1 — caller attempt is itself inside another mission."""
    return _depth(ctx) > 1


def _always(ctx: ResolverContext) -> bool:
    """Total-coverage tail predicate — always True regardless of context."""
    return True


def register_builtin_predicates() -> None:
    """Idempotent — safe to call from app startup."""
    PredicateRegistry.register(
        "nested_mission_depth_within_handoff_range",
        _nested_mission_depth_within_handoff_range,
    )
    PredicateRegistry.register(
        "nested_mission_depth_above_handoff_range",
        _nested_mission_depth_above_handoff_range,
    )
    PredicateRegistry.register(
        "nested_mission_depth_gt_1",
        _nested_mission_depth_gt_1,
    )
    PredicateRegistry.register("always", _always)
