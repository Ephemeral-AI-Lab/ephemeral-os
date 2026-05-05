"""Mission / episode context block builders shared by role recipes."""

from __future__ import annotations

from task_center.mission.mission import Mission
from task_center.context_engine.errors import ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.episode.episode import Episode

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
                    "episode_sequence_no": str(prior.sequence_no),
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
                    "episode_sequence_no": str(prior.sequence_no),
                    "subheading": f"Episode {prior.sequence_no} summary",
                },
            )
        )
    return out


__all__ = [
    "CURRENT_EPISODE_HEADING",
    "MISSION_EPISODE_HEADING",
    "MISSION_HEADING",
    "PREVIOUS_EPISODE_RESULTS_HEADING",
    "mission_episode_blocks",
]
