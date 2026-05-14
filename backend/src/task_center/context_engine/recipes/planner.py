"""``planner`` recipe — context for one attempt planner spawn.

See plan §3.3.6 for the full block taxonomy. The recipe reads:

* the mission / current episode frame;
* every prior closed-succeeded episode projection for episode 2+;
* every failed attempt in the current episode except the running one
  (``failed_attempt_landscape`` blocks, ordered by ``attempt_sequence_no``).

The recipe is a pure builder: it reads stores and returns a
:class:`ContextPacket`. No renderer calls, no lifecycle mutations.

Also contains the ``advisor`` and ``resolver`` helper recipes (absorbed from
``helper.py``). Helper agents (advisor, resolver) are spawned by parent agents
via tools (``ask_advisor`` / ``run_subagent``). They inherit the parent's full
:class:`ContextPacket` so they reason inside the parent's frame, not in
isolation. See plan §3.3.8.

Inheritance policy: every parent block is copied with priority demoted by
exactly one level (``required → high → medium → low → low``). Inherited blocks
carry ``metadata['inherited_from_parent'] = 'true'`` so the renderer can group
them under a ``# Parent context`` heading. The concrete helper request is
appended by the helper tool after composition.
"""

from __future__ import annotations

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes.attempt_landscape import (
    failed_attempt_landscape_blocks,
)
from task_center.context_engine.recipes.generator import (
    mission_episode_blocks,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

# ---------------------------------------------------------------------------
# Helper recipes (advisor + resolver) — absorbed from helper.py
# ---------------------------------------------------------------------------

ADVISOR_ID = "advisor"
RESOLVER_ID = "resolver"

_HELPER_REQUIRED_FIELDS = frozenset(
    {"mission_id", "task_id", "parent_packet_id"}
)

_DEMOTION = {
    ContextPriority.REQUIRED: ContextPriority.HIGH,
    ContextPriority.HIGH: ContextPriority.MEDIUM,
    ContextPriority.MEDIUM: ContextPriority.LOW,
    ContextPriority.LOW: ContextPriority.LOW,
}


def demote_priority(priority: ContextPriority) -> ContextPriority:
    return _DEMOTION[priority]


def _build_helper_packet(
    *,
    target_role: str,
    scope: ContextScope,
    deps: ContextEngineDeps,
) -> ContextPacket:
    # Engine pre-validates required scope fields via ``assert_fields``; this
    # explicit guard makes the recipe self-defending under ``python -O`` where
    # ``assert`` would be stripped.
    if (
        scope.mission_id is None
        or scope.task_id is None
        or scope.parent_packet_id is None
    ):
        raise ContextEngineError(
            "Helper recipes require mission_id, task_id, and parent_packet_id; "
            f"got {scope!r}"
        )
    if deps.context_packet_store is None:
        raise ContextEngineError(
            "Helper recipes require ContextEngineDeps.context_packet_store; "
            "wire ContextPacketStore through app startup."
        )
    parent_packet = deps.context_packet_store.get(scope.parent_packet_id)
    if parent_packet is None:
        raise ContextEngineError(
            f"Parent packet {scope.parent_packet_id!r} not found"
        )
    blocks: list[ContextBlock] = []
    for parent_block in parent_packet.blocks:
        demoted = demote_priority(parent_block.priority)
        inherited_meta = {
            **parent_block.metadata,
            "inherited_from_parent": "true",
        }
        blocks.append(
            ContextBlock(
                kind=parent_block.kind,
                priority=demoted,
                text=parent_block.text,
                source_id=parent_block.source_id,
                source_kind=parent_block.source_kind,
                metadata=inherited_meta,
            )
        )
    return ContextPacket(
        target_role=target_role,
        target_id=scope.task_id,
        canonical_refs=ContextRefs(
            mission_id=scope.mission_id,
            task_id=scope.task_id,
        ),
        blocks=blocks,
        metadata={"inherits_from": parent_packet.id},
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


def _advisor_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    return _build_helper_packet(
        target_role="advisor", scope=scope, deps=deps
    )


def _resolver_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    return _build_helper_packet(
        target_role="resolver", scope=scope, deps=deps
    )


ADVISOR_RECIPE = ContextRecipe(
    id=ADVISOR_ID,
    required_scope_fields=_HELPER_REQUIRED_FIELDS,
    build=_advisor_build,
)

RESOLVER_RECIPE = ContextRecipe(
    id=RESOLVER_ID,
    required_scope_fields=_HELPER_REQUIRED_FIELDS,
    build=_resolver_build,
)

# ---------------------------------------------------------------------------
# planner recipe
# ---------------------------------------------------------------------------

PLANNER_ID = "planner"
_REQUIRED_FIELDS = frozenset(
    {"mission_id", "episode_id", "attempt_id"}
)


def _planner_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    # Engine pre-validates required scope fields via ``assert_fields``; this
    # explicit guard makes the recipe self-defending under ``python -O`` where
    # ``assert`` would be stripped.
    if (
        scope.mission_id is None
        or scope.episode_id is None
        or scope.attempt_id is None
    ):
        raise ContextEngineError(
            "planner requires mission_id, episode_id, and attempt_id; "
            f"got {scope!r}"
        )
    mission = deps.mission_store.get(scope.mission_id)
    if mission is None:
        raise ContextEngineError(
            f"Mission {scope.mission_id!r} not found"
        )
    episode = deps.episode_store.get(scope.episode_id)
    if episode is None:
        raise ContextEngineError(
            f"Episode {scope.episode_id!r} not found"
        )

    blocks = mission_episode_blocks(
        mission=mission,
        current_episode=episode,
        episodes=deps.episode_store.list_for_mission(mission.id),
    )

    blocks.extend(
        failed_attempt_landscape_blocks(
            current_attempt_id=scope.attempt_id,
            attempts=deps.attempt_store.list_for_episode(episode.id),
            task_store=deps.task_store,
        )
    )

    return ContextPacket(
        target_role="planner",
        target_id=scope.attempt_id,
        canonical_refs=ContextRefs(
            mission_id=mission.id,
            episode_id=episode.id,
            attempt_id=scope.attempt_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


PLANNER_RECIPE = ContextRecipe(
    id=PLANNER_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_planner_build,
)
