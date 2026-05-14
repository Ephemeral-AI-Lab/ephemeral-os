"""Episode-closure routing — dispatches :class:`EpisodeClosureReport`.

Carved out of :class:`MissionHandler`. The router branches on the
``EpisodeClosureReport.outcome`` discriminated union and either:

- ``SuccessContinue`` → ask :class:`EpisodeFactory` for a continuation
  episode, then start its initial attempt; close the mission failed if
  startup fails.
- ``TerminalSuccess`` → close the mission as succeeded.
- ``AttemptPlanFailed`` → close the mission as failed.

The router is deliberately stateless other than its dependencies; this
keeps episode-closed routing testable in isolation from the rest of the
mission boundary.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from task_center.episode.closure_report import (
    AttemptPlanFailed,
    EpisodeClosureReport,
    SuccessContinue,
    TerminalSuccess,
)
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.episode.state import EpisodeStatus
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.mission.episode_factory import EpisodeFactory
from task_center.persistence import EpisodeStoreProtocol

logger = logging.getLogger(__name__)


class EpisodeClosureRouter:
    """Routes :class:`EpisodeClosureReport` to continuation or closure."""

    def __init__(
        self,
        *,
        factory: EpisodeFactory,
        episode_store: EpisodeStoreProtocol,
        manager_registry: EpisodeManagerRegistry,
        close_mission,
    ) -> None:
        self._factory = factory
        self._episode_store = episode_store
        self._manager_registry = manager_registry
        # Callable[[*, mission_id, succeeded, final_episode_id, final_attempt_id], None]
        self._close_mission = close_mission

    def route(self, report: EpisodeClosureReport) -> None:
        episode = self._episode_store.get(report.episode_id)
        if episode is None:
            raise TaskCenterInvariantViolation(
                f"Episode {report.episode_id!r} not found"
            )
        try:
            outcome = report.outcome
            if isinstance(outcome, SuccessContinue):
                (
                    next_episode,
                    next_manager,
                ) = self._factory.create_continuation(
                    previous_episode=episode
                )
                self._start_continuation(
                    next_episode=next_episode,
                    next_manager=next_manager,
                    previous_report=report,
                )
            elif isinstance(outcome, TerminalSuccess):
                self._close_mission(
                    mission_id=episode.mission_id,
                    succeeded=True,
                    final_episode_id=episode.id,
                    final_attempt_id=report.final_attempt_id,
                )
            elif isinstance(outcome, AttemptPlanFailed):
                self._close_mission(
                    mission_id=episode.mission_id,
                    succeeded=False,
                    final_episode_id=episode.id,
                    final_attempt_id=report.final_attempt_id,
                )
            else:  # pragma: no cover - exhaustive over discriminated union
                raise TaskCenterInvariantViolation(
                    f"Unknown ClosureOutcome: {outcome!r}"
                )
        finally:
            self._manager_registry.deregister(episode.id)

    # ---- internal --------------------------------------------------------

    def _start_continuation(
        self,
        *,
        next_episode,
        next_manager,
        previous_report: EpisodeClosureReport,
    ) -> None:
        """Create and start the continuation episode's initial attempt.

        Skipped when the factory has no orchestrator wired (test mode); the
        caller is responsible for driving the attempt manually. Production
        always wires the factory so continuation startup runs end-to-end.

        On startup failure the continuation episode is cancelled and the
        mission is closed as failed. If attempt insertion happened, the
        close report points at that failed continuation attempt.
        """
        if not self._factory.has_orchestrator_factory:
            return
        try:
            next_manager.create_initial_attempt()
        except Exception:
            failed_attempt_id = (
                self._latest_attempt_id_for_episode(next_episode.id)
                or previous_report.final_attempt_id
            )
            self._episode_store.set_status(
                next_episode.id,
                status=EpisodeStatus.CANCELLED,
                closed_at=datetime.now(UTC),
            )
            self._manager_registry.deregister(next_episode.id)
            self._close_mission(
                mission_id=next_episode.mission_id,
                succeeded=False,
                final_episode_id=next_episode.id,
                final_attempt_id=failed_attempt_id,
            )

    def _latest_attempt_id_for_episode(
        self, episode_id: str
    ) -> str | None:
        episode = self._episode_store.get(episode_id)
        if episode is None:
            return None
        return episode.latest_attempt_id


__all__ = ["EpisodeClosureRouter"]
