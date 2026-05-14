"""``generator`` recipe — context for one generator task spawn.

Emits the current attempt plan, dependency results, and the assigned local task
in presentation order. The assigned task is required but remains last so the
generator ends on its concrete obligation.

Also contains the mission/episode context block builders shared by role
recipes (formerly ``mission_episode.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes import latest_summary_text
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope
from task_center.episode.state import Episode
from task_center.mission.state import Mission

if TYPE_CHECKING:
    from task_center._core.persistence import TaskStoreProtocol

# ---------------------------------------------------------------------------
# Mission / episode frame builders (absorbed from mission_episode.py)
# ---------------------------------------------------------------------------

MISSION_EPISODE_HEADING = "# Mission / Current Episode"
MISSION_HEADING = "# Mission"
CURRENT_EPISODE_HEADING = "# Current Episode"
PREVIOUS_EPISODE_RESULTS_HEADING = "# Previous Episode Results"


def mission_episode_blocks(
    *,
    mission: Mission,
    current_episode: Episode,
    episodes: list[Episode],
) -> list[ContextBlock]:
    """Return the mission/episode frame in LLM-facing semantic order."""
    if current_episode.sequence_no == 1:
        return [_episode_goal_block(current_episode, heading=MISSION_EPISODE_HEADING)]

    return [
        _mission_goal_block(mission),
        *_previous_episode_result_blocks(
            current=current_episode,
            episodes=episodes,
        ),
        _episode_goal_block(
            current_episode,
            heading=CURRENT_EPISODE_HEADING,
        ),
    ]


def _episode_goal_block(episode: Episode, *, heading: str) -> ContextBlock:
    return ContextBlock(
        kind=ContextBlockKind.EPISODE_GOAL,
        priority=ContextPriority.REQUIRED,
        text=episode.goal,
        source_id=episode.id,
        source_kind="episode",
        metadata={"heading": heading},
    )


def _mission_goal_block(mission: Mission) -> ContextBlock:
    return ContextBlock(
        kind=ContextBlockKind.MISSION_GOAL,
        priority=ContextPriority.REQUIRED,
        text=mission.goal,
        source_id=mission.id,
        source_kind="mission",
        metadata={"heading": MISSION_HEADING},
    )


def _previous_episode_result_blocks(
    *,
    current: Episode,
    episodes: list[Episode],
) -> list[ContextBlock]:
    priors = sorted(
        (s for s in episodes if s.sequence_no < current.sequence_no),
        key=lambda s: s.sequence_no,
    )
    out: list[ContextBlock] = []
    immediate_prior_sequence = current.sequence_no - 1
    for prior in priors:
        if prior.task_specification is None or prior.task_summary is None:
            raise ContextEngineError(
                f"Prior episode {prior.id!r} (seq={prior.sequence_no}) is "
                "missing task_specification or task_summary; chain "
                "integrity violated."
            )
        priority = (
            ContextPriority.HIGH
            if prior.sequence_no == immediate_prior_sequence
            else ContextPriority.MEDIUM
        )
        base_meta = {
            "episode_sequence_no": str(prior.sequence_no),
            "group_heading": PREVIOUS_EPISODE_RESULTS_HEADING,
        }
        out.append(
            ContextBlock(
                kind=ContextBlockKind.PRIOR_EPISODE_SPECIFICATION,
                priority=priority,
                text=prior.task_specification,
                source_id=prior.id,
                source_kind="episode",
                metadata={
                    **base_meta,
                    "subheading": f"Episode {prior.sequence_no} accepted plan",
                },
            )
        )
        out.append(
            ContextBlock(
                kind=ContextBlockKind.PRIOR_EPISODE_SUMMARY,
                priority=priority,
                text=prior.task_summary,
                source_id=prior.id,
                source_kind="episode",
                metadata={
                    **base_meta,
                    "subheading": f"Episode {prior.sequence_no} summary",
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# generator recipe
# ---------------------------------------------------------------------------

GENERATOR_ID = "generator"
_REQUIRED_FIELDS = frozenset({"mission_id", "attempt_id", "task_id"})


def _generator_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    # Engine pre-validates required scope fields via ``assert_fields``; this
    # explicit guard makes the recipe self-defending under ``python -O`` where
    # ``assert`` would be stripped.
    if (
        scope.mission_id is None
        or scope.attempt_id is None
        or scope.task_id is None
    ):
        raise ContextEngineError(
            "generator requires mission_id, attempt_id, and task_id; "
            f"got {scope!r}"
        )
    attempt = deps.attempt_store.get(scope.attempt_id)
    if attempt is None:
        raise ContextEngineError(
            f"Attempt {scope.attempt_id!r} not found"
        )
    task = deps.task_store.get_task(scope.task_id)
    if task is None:
        raise ContextEngineError(
            f"TaskCenterTask {scope.task_id!r} not found"
        )

    blocks: list[ContextBlock] = []
    if attempt.task_specification:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.TASK_SPECIFICATION,
                priority=ContextPriority.HIGH,
                text=attempt.task_specification,
                source_id=attempt.id,
                source_kind="attempt",
            )
        )

    blocks.extend(
        _dependency_summary_blocks(
            needs=tuple(str(dep) for dep in task.get("needs") or ()),
            task_store=deps.task_store,
        )
    )
    blocks.append(
        ContextBlock(
            kind=ContextBlockKind.PLANNED_TASK_SPEC,
            priority=ContextPriority.REQUIRED,
            text=str(task.get("rendered_prompt") or ""),
            source_id=scope.task_id,
            source_kind="task_center_task",
        )
    )

    return ContextPacket(
        target_role="generator",
        target_id=scope.task_id,
        canonical_refs=ContextRefs(
            mission_id=scope.mission_id,
            episode_id=scope.episode_id or attempt.episode_id,
            attempt_id=scope.attempt_id,
            task_id=scope.task_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


def _dependency_summary_blocks(
    *,
    needs: tuple[str, ...],
    task_store: TaskStoreProtocol,
) -> list[ContextBlock]:
    out: list[ContextBlock] = []
    for dep_id in needs:
        dep = task_store.get_task(dep_id)
        if dep is None:
            # ``needs`` are persisted DAG edges validated at planner-submission
            # acceptance; a missing row here is a harness invariant violation,
            # not a tolerable absence. Surface it so the LLM never reasons
            # over a silently-truncated dependency frame.
            raise ContextEngineError(
                f"Dependency task {dep_id!r} referenced by needs is missing; "
                "generator context cannot be assembled without dependency results."
            )
        out.append(
            ContextBlock(
                kind=ContextBlockKind.DEPENDENCY_SUMMARY,
                priority=ContextPriority.MEDIUM,
                text=latest_summary_text(dep.get("summaries")),
                source_id=dep_id,
                source_kind="task_center_task",
                metadata={
                    "dep_id": dep_id,
                    "group_heading": "# Dependency Results",
                    "subheading": str(dep.get("id") or dep_id),
                },
            )
        )
    return out


GENERATOR_RECIPE = ContextRecipe(
    id=GENERATOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_generator_build,
)
