"""``planner`` recipe — context for one attempt planner spawn.

See plan §3.3.6 for the full block taxonomy. The recipe reads:

* the mission / current episode frame;
* every prior closed-succeeded episode projection for episode 2+;
* every failed attempt in the current episode except the running one
  (``failed_attempt_landscape`` blocks, ordered by ``attempt_sequence_no``).
"""

from __future__ import annotations

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import ContextPacket, ContextRefs
from task_center.context_engine.recipes._shared import mission_episode_blocks
from task_center.context_engine.recipes.attempt_landscape import (
    failed_attempt_landscape_blocks,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

PLANNER_ID = "planner"
_REQUIRED_FIELDS = frozenset({"goal_id", "iteration_id", "attempt_id"})


def _planner_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    mission = deps.mission_store.get(scope.goal_id)
    if mission is None:
        raise ContextEngineError(f"Mission {scope.goal_id!r} not found")
    episode = deps.episode_store.get(scope.iteration_id)
    if episode is None:
        raise ContextEngineError(f"Episode {scope.iteration_id!r} not found")

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
            goal_id=mission.id,
            iteration_id=episode.id,
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
