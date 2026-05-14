"""Process-local registry: one :class:`EpisodeManager` per open ``Episode``.

The registry stores objects implementing :class:`RegisteredEpisodeManager`
(structurally satisfied by :class:`EpisodeManager`). Protocol-based typing
avoids the runtime cycle that would form if this module imported
:mod:`task_center.episode.manager` directly.
"""

from __future__ import annotations

from task_center.exceptions import TaskCenterInvariantViolation
from task_center.protocols import RegisteredEpisodeManager


class EpisodeManagerRegistry:
    """In-memory registry enforcing one-manager-per-open-episode."""

    def __init__(self) -> None:
        self._by_episode_id: dict[str, RegisteredEpisodeManager] = {}

    def register(self, manager: RegisteredEpisodeManager) -> None:
        episode_id = manager.episode_id
        if episode_id in self._by_episode_id:
            raise TaskCenterInvariantViolation(
                f"EpisodeManager already registered for episode {episode_id!r}"
            )
        self._by_episode_id[episode_id] = manager

    def get(self, episode_id: str) -> RegisteredEpisodeManager | None:
        return self._by_episode_id.get(episode_id)

    def deregister(self, episode_id: str) -> None:
        self._by_episode_id.pop(episode_id, None)
