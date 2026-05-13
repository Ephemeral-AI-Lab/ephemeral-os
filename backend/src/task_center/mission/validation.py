"""Mission-layer invariants. All raise ``TaskCenterInvariantViolation``."""

from __future__ import annotations

from task_center.mission.mission import Mission
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.episode.episode import Episode, EpisodeStatus


def assert_mission_open(mission: Mission) -> None:
    if not mission.is_open:
        raise TaskCenterInvariantViolation(
            f"Mission {mission.id!r} is not open (status={mission.status})"
        )


def assert_episode_id_unique_in_mission(
    mission: Mission, episode_id: str
) -> None:
    if episode_id in mission.episode_ids:
        raise TaskCenterInvariantViolation(
            f"Episode {episode_id!r} already present in Mission "
            f"{mission.id!r} episode list"
        )


def assert_episode_sequence_contiguous(
    mission: Mission, new_sequence_no: int
) -> None:
    expected = len(mission.episode_ids) + 1
    if new_sequence_no != expected:
        raise TaskCenterInvariantViolation(
            f"Episode sequence_no must be contiguous: expected {expected}, "
            f"got {new_sequence_no}"
        )


def assert_continuation_episode_predecessor(previous: Episode) -> None:
    if previous.status != EpisodeStatus.SUCCEEDED:
        raise TaskCenterInvariantViolation(
            f"Continuation requires predecessor episode {previous.id!r} to be "
            f"SUCCEEDED, not {previous.status}"
        )
    if previous.continuation_goal is None:
        raise TaskCenterInvariantViolation(
            f"Continuation requires predecessor episode {previous.id!r} to have a "
            f"continuation_goal; none was recorded"
        )
