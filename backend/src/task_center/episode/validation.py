"""Episode-layer invariants. All raise ``TaskCenterInvariantViolation``."""

from __future__ import annotations

from task_center.exceptions import TaskCenterInvariantViolation
from task_center.attempt.state import Attempt
from task_center.episode.episode import Episode


def assert_episode_open(episode: Episode) -> None:
    if not episode.is_open:
        raise TaskCenterInvariantViolation(
            f"Episode {episode.id!r} is not open (status={episode.status})"
        )


def assert_episode_has_budget(episode: Episode) -> None:
    if not episode.has_budget_remaining:
        raise TaskCenterInvariantViolation(
            f"Episode {episode.id!r} attempt budget exhausted "
            f"({episode.attempt_count}/{episode.attempt_budget})"
        )


def assert_attempt_belongs_to_episode(
    attempt: Attempt, episode: Episode
) -> None:
    if attempt.episode_id != episode.id:
        raise TaskCenterInvariantViolation(
            f"Attempt {attempt.id!r} (episode {attempt.episode_id!r}) "
            f"does not belong to Episode {episode.id!r}"
        )
