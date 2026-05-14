"""``planner`` recipe — context for one attempt planner spawn.

See plan §3.3.6 for the full block taxonomy. The recipe reads:

* the mission / current episode frame;
* every prior closed-succeeded episode projection for episode 2+;
* every failed attempt in the current episode except the running one
  (``failed_attempt_landscape`` blocks, ordered by ``attempt_sequence_no``).

The recipe is a pure builder: it reads stores and returns a
:class:`ContextPacket`. No renderer calls, no lifecycle mutations.
"""

from __future__ import annotations

from task_center.context_engine.engine import ContextEngineDeps
from task_center.context_engine.errors import ContextEngineError
from task_center.context_engine.packet import (
    ContextPacket,
    ContextRefs,
)
from task_center.context_engine.recipes.mission_episode import (
    mission_episode_blocks,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.recipes.attempt_landscape import (
    failed_attempt_landscape_blocks,
)
from task_center.context_engine.scope import ContextScope

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
