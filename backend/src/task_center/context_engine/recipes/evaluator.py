"""``evaluator_v1`` recipe — context for one evaluator spawn.

Emits mission/episode framing, the current attempt plan, dependency results,
and the evaluation criteria in presentation order. The criteria block remains
last so pass/fail authority is anchored to the current attempt contract.
"""

from __future__ import annotations

from task_center.context_engine.engine import ContextEngineDeps
from task_center.context_engine.errors import ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes._summaries import latest_summary_text
from task_center.context_engine.recipes._mission_episode import (
    mission_episode_blocks,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

EVALUATOR_V1 = "evaluator_v1"
_REQUIRED_FIELDS = frozenset({"mission_id", "attempt_id"})


def _evaluator_v1_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    assert scope.mission_id is not None
    assert scope.attempt_id is not None
    attempt = deps.attempt_store.get(scope.attempt_id)
    if attempt is None:
        raise ContextEngineError(
            f"Attempt {scope.attempt_id!r} not found"
        )
    mission = deps.mission_store.get(scope.mission_id)
    if mission is None:
        raise ContextEngineError(
            f"Mission {scope.mission_id!r} not found"
        )
    episode_id = scope.episode_id or attempt.episode_id
    episode = deps.episode_store.get(episode_id)
    if episode is None:
        raise ContextEngineError(f"Episode {episode_id!r} not found")

    blocks = mission_episode_blocks(
        mission=mission,
        current_episode=episode,
        episodes=deps.episode_store.list_for_mission(mission.id),
    )
    if attempt.task_specification:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.TASK_SPECIFICATION,
                priority=ContextPriority.REQUIRED,
                text=attempt.task_specification,
                source_id=attempt.id,
                source_kind="attempt",
            )
        )

    for task_id in attempt.generator_task_ids:
        task = deps.task_store.get_task(task_id)
        if task is None:
            continue
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.COMPLETED_TASK_SUMMARY,
                priority=ContextPriority.HIGH,
                text=latest_summary_text(task.get("summaries")),
                source_id=task_id,
                source_kind="task_center_task",
                metadata={
                    "task_id": task_id,
                    "group_heading": "# Dependency Results",
                    "subheading": str(task.get("id") or task_id),
                },
            )
        )
    criteria = list(attempt.evaluation_criteria)
    if criteria:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.EVALUATION_CRITERIA,
                priority=ContextPriority.REQUIRED,
                text="\n".join(f"- {c}" for c in criteria),
                source_id=attempt.id,
                source_kind="attempt",
            )
        )

    return ContextPacket(
        target_role="evaluator",
        target_id=scope.attempt_id,
        canonical_refs=ContextRefs(
            mission_id=scope.mission_id,
            episode_id=episode.id,
            attempt_id=scope.attempt_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


EVALUATOR_V1_RECIPE = ContextRecipe(
    id=EVALUATOR_V1,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_evaluator_v1_build,
)
