"""Runtime dependency seam for harness attempt orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from db.stores.mission_store import MissionStore
from db.stores.attempt_store import AttemptStore
from db.stores.task_center_store import TaskCenterStore
from db.stores.episode_store import EpisodeStore
from task_center.config import HarnessLifecycleConfig
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.attempt.state import Attempt
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.task import HarnessTaskRole

if TYPE_CHECKING:
    from task_center.context_engine.composer import ContextComposer
    from task_center.entry.controller import EntryTaskController
    from task_center.attempt.orchestrator_registry import (
        AttemptOrchestratorRegistry,
    )


@dataclass(frozen=True, slots=True)
class AgentLaunch:
    task_id: str
    task_center_run_id: str
    attempt_id: str | None
    role: HarnessTaskRole
    agent_name: str
    task_input: str
    needs: tuple[str, ...]
    context_packet_id: str | None = None
    mission_id: str | None = None


class AttemptAgentLauncher(Protocol):
    """Launches or queues one harness agent task."""

    def launch(self, launch: AgentLaunch) -> None: ...


@dataclass(frozen=True, slots=True)
class AttemptRuntime:
    mission_store: MissionStore
    episode_store: EpisodeStore
    attempt_store: AttemptStore
    task_store: TaskCenterStore
    agent_launcher: AttemptAgentLauncher
    orchestrator_registry: "AttemptOrchestratorRegistry"
    manager_registry: EpisodeManagerRegistry | None = None
    lifecycle_config: HarnessLifecycleConfig = field(default_factory=HarnessLifecycleConfig)
    # When set, orchestrator + dispatcher route launches through the composer
    # to obtain a rendered task_input + selected agent definition.
    # Optional so existing tests can continue without composer wiring.
    composer: "ContextComposer | None" = None
    # Lifecycle controller for the attempt-less entry executor. ``None`` for
    # delegated-only runtimes (mission starter builds its own runtime
    # with no controller because delegated requests always have a attempt).
    # The close-report router and launcher use this to dispatch lifecycle
    # events for entry tasks whose ``task_center_attempt_id`` is None.
    entry_task_controller: "EntryTaskController | None" = None

    def task_center_run_id_for_attempt(self, attempt: Attempt) -> str:
        episode = self.episode_store.get(attempt.episode_id)
        if episode is None:
            raise TaskCenterInvariantViolation(
                f"Episode {attempt.episode_id!r} not found for "
                f"Attempt {attempt.id!r}"
            )
        request = self.mission_store.get(episode.mission_id)
        if request is None:
            raise TaskCenterInvariantViolation(
                f"Mission {episode.mission_id!r} not "
                f"found for Episode {episode.id!r}"
            )
        return request.task_center_run_id

    def require_composer(self) -> "ContextComposer":
        if self.composer is None:
            raise TaskCenterInvariantViolation(
                "AttemptRuntime requires a ContextComposer for harness "
                "agent launches; none was wired."
            )
        return self.composer

    def entry_task_controller_for(
        self, task_id: str
    ) -> "EntryTaskController | None":
        """Return the entry controller iff it's bound to *task_id*.

        Used at the four entry-mode dispatch sites (mission starter
        parent-waiting + compensation + duplicate-child check, close-report
        router, submission resolver) so each site collapses to one call
        instead of duplicating the ``is not None and task_id == X`` guard.
        Returns ``None`` for attempt-mode tasks or when no controller is
        wired.
        """
        controller = self.entry_task_controller
        if controller is None or controller.task_id != task_id:
            return None
        return controller
