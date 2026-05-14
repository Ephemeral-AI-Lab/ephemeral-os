"""MissionHandler — mission boundary facade.

Composes the four mission-boundary verbs into a single class for callers
that want one wiring point. Internally delegates to:

- :class:`MissionRepository` — mission CRUD + closure write.
- :class:`EpisodeFactory` — initial + continuation episode creation.
- :class:`EpisodeClosureRouter` — :class:`EpisodeClosureReport` routing.

The constructor signature is preserved for backward compatibility with
existing call sites (``MissionStarter._build_handler``) and tests.
"""

from __future__ import annotations

from collections.abc import Callable

from task_center.config import TaskCenterLifecycleConfig
from task_center.episode.closure_report import EpisodeClosureReport
from task_center.episode.manager import EpisodeManager, OrchestratorFactory
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.episode.state import Episode
from task_center.mission.episode_closure_router import EpisodeClosureRouter
from task_center.mission.episode_factory import EpisodeFactory
from task_center.mission.repository import MissionRepository
from task_center.mission.state import Mission, MissionClosureReport
from task_center.persistence import (
    AttemptStoreProtocol,
    EpisodeStoreProtocol,
    MissionStoreProtocol,
    TaskStoreProtocol,
)


MissionClosureReportSink = Callable[[MissionClosureReport], None]


class MissionHandler:
    """Facade composing :class:`MissionRepository`, :class:`EpisodeFactory`,
    and :class:`EpisodeClosureRouter`. Owns the mission boundary's wiring.
    """

    def __init__(
        self,
        *,
        mission_store: MissionStoreProtocol,
        episode_store: EpisodeStoreProtocol,
        attempt_store: AttemptStoreProtocol,
        manager_registry: EpisodeManagerRegistry,
        config: TaskCenterLifecycleConfig,
        deliver_closure_report: MissionClosureReportSink | None = None,
        orchestrator_factory: OrchestratorFactory | None = None,
        task_store: TaskStoreProtocol | None = None,
    ) -> None:
        self._deliver_closure_report = deliver_closure_report
        # Retained as an attribute for tests that introspect the handler's
        # wiring. The factory below owns the live orchestrator factory; the
        # ``_orchestrator_factory`` property proxies reads/writes through it.
        self._manager_registry = manager_registry
        self._repository = MissionRepository(mission_store)
        self._factory = EpisodeFactory(
            mission_repository=self._repository,
            episode_store=episode_store,
            attempt_store=attempt_store,
            manager_registry=manager_registry,
            config=config,
            on_episode_closed=self.handle_episode_closed,
            orchestrator_factory=orchestrator_factory,
            task_store=task_store,
        )
        self._router = EpisodeClosureRouter(
            factory=self._factory,
            episode_store=episode_store,
            manager_registry=manager_registry,
            close_mission=self.close_mission,
        )

    # ---- backward-compat introspection ----------------------------------

    @property
    def _orchestrator_factory(self) -> OrchestratorFactory | None:
        return self._factory._orchestrator_factory

    @_orchestrator_factory.setter
    def _orchestrator_factory(
        self, value: OrchestratorFactory | None
    ) -> None:
        # Tests inject failing factories after construction; route the
        # mutation into the factory so its has_orchestrator_factory + spawn
        # path see the override.
        self._factory._orchestrator_factory = value

    # ---- public API (preserved signatures) ------------------------------

    def create_mission(
        self,
        *,
        task_center_run_id: str,
        requested_by_task_id: str,
        goal: str,
    ) -> Mission:
        return self._repository.create(
            task_center_run_id=task_center_run_id,
            requested_by_task_id=requested_by_task_id,
            goal=goal,
        )

    def create_initial_episode_with_manager(
        self, *, mission_id: str
    ) -> tuple[Episode, EpisodeManager]:
        return self._factory.create_initial(mission_id=mission_id)

    def create_continuation_episode_with_manager(
        self, *, previous_episode: Episode
    ) -> tuple[Episode, EpisodeManager]:
        return self._factory.create_continuation(
            previous_episode=previous_episode
        )

    def handle_episode_closed(
        self, report: EpisodeClosureReport
    ) -> None:
        self._router.route(report)

    def close_mission(
        self,
        *,
        mission_id: str,
        succeeded: bool,
        final_episode_id: str,
        final_attempt_id: str | None,
    ) -> Mission:
        updated, report = self._repository.close(
            mission_id=mission_id,
            succeeded=succeeded,
            final_episode_id=final_episode_id,
            final_attempt_id=final_attempt_id,
        )
        if self._deliver_closure_report is not None:
            self._deliver_closure_report(report)
        return updated


__all__ = ["MissionHandler", "MissionClosureReportSink"]
