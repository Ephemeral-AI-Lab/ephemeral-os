"""Episode + EpisodeManager creation factory.

Carved out of :class:`MissionHandler`. Owns the two episode-creation verbs:

- ``create_initial`` — first episode for a fresh mission.
- ``create_continuation`` — sequence-no+1 episode after a partial-plan
  success.

Both verbs spawn the :class:`EpisodeManager` for the newly created episode
and register it on the shared :class:`EpisodeManagerRegistry`. The factory
is intentionally aware of the registry so callers don't have to remember
to register every new manager.
"""

from __future__ import annotations

from task_center.config import TaskCenterLifecycleConfig
from task_center.episode.closure_report import EpisodeClosureReport
from task_center.episode.manager import EpisodeManager, OrchestratorFactory
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.episode.state import (
    Episode,
    EpisodeCreationReason,
)
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.invariants import (
    assert_continuation_episode_predecessor,
    assert_episode_sequence_contiguous,
    assert_mission_open,
)
from task_center.mission.repository import MissionRepository
from task_center.mission.state import Mission
from task_center.persistence import (
    AttemptStoreProtocol,
    EpisodeStoreProtocol,
    TaskStoreProtocol,
)


class EpisodeFactory:
    """Creates :class:`Episode` rows + their :class:`EpisodeManager`."""

    def __init__(
        self,
        *,
        mission_repository: MissionRepository,
        episode_store: EpisodeStoreProtocol,
        attempt_store: AttemptStoreProtocol,
        manager_registry: EpisodeManagerRegistry,
        config: TaskCenterLifecycleConfig,
        on_episode_closed,
        orchestrator_factory: OrchestratorFactory | None = None,
        task_store: TaskStoreProtocol | None = None,
    ) -> None:
        self._mission_repository = mission_repository
        self._episode_store = episode_store
        self._attempt_store = attempt_store
        self._manager_registry = manager_registry
        self._config = config
        self._on_episode_closed = on_episode_closed
        self._orchestrator_factory = orchestrator_factory
        self._task_store = task_store

    def create_initial(
        self, *, mission_id: str
    ) -> tuple[Episode, EpisodeManager]:
        mission = self._mission_repository.require(mission_id)
        assert_mission_open(mission)
        assert_episode_sequence_contiguous(mission, new_sequence_no=1)
        episode = self._episode_store.insert(
            mission_id=mission_id,
            sequence_no=1,
            creation_reason=EpisodeCreationReason.INITIAL,
            goal=mission.goal,
            attempt_budget=self._config.default_attempt_budget,
        )
        self._mission_repository.append_episode_id(mission, episode.id)
        manager = self._spawn_manager(episode)
        return episode, manager

    def create_continuation(
        self, *, previous_episode: Episode
    ) -> tuple[Episode, EpisodeManager]:
        mission = self._mission_repository.require(previous_episode.mission_id)
        assert_mission_open(mission)
        assert_continuation_episode_predecessor(previous_episode)
        new_sequence_no = previous_episode.sequence_no + 1
        assert_episode_sequence_contiguous(
            mission, new_sequence_no=new_sequence_no
        )
        # Narrowed by ``assert_continuation_episode_predecessor`` above; the
        # explicit check makes the invariant self-defending under
        # ``python -O`` where ``assert`` would be stripped.
        if previous_episode.continuation_goal is None:
            raise TaskCenterInvariantViolation(
                f"Previous episode {previous_episode.id!r} has no "
                "continuation_goal despite passing the predecessor "
                "invariant."
            )
        episode = self._episode_store.insert(
            mission_id=mission.id,
            sequence_no=new_sequence_no,
            creation_reason=EpisodeCreationReason.PARTIAL_CONTINUATION,
            goal=previous_episode.continuation_goal,
            attempt_budget=self._config.default_attempt_budget,
        )
        self._mission_repository.append_episode_id(mission, episode.id)
        manager = self._spawn_manager(episode)
        return episode, manager

    @property
    def has_orchestrator_factory(self) -> bool:
        return self._orchestrator_factory is not None

    # ---- internal --------------------------------------------------------

    def _spawn_manager(self, episode: Episode) -> EpisodeManager:
        manager = EpisodeManager(
            episode_id=episode.id,
            episode_store=self._episode_store,
            attempt_store=self._attempt_store,
            on_episode_closed=self._on_episode_closed,
            orchestrator_factory=self._orchestrator_factory,
            task_store=self._task_store,
        )
        self._manager_registry.register(manager)
        return manager


__all__ = ["EpisodeFactory"]
