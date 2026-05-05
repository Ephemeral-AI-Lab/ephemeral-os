"""MissionStarter — use-case boundary for delegated request start.

Composes the existing request, episode, manager, and parent-task owners into
the single safe mission-start path used by ``request_mission_solution``. Owns
parent-task CAS, deferred orchestrator startup, and compensation on failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from task_center.mission.close_report_delivery import (
    MissionCloseReportRouter,
)
from task_center.mission.handler import MissionHandler
from task_center.mission.mission import (
    MissionCloseReport,
    Mission,
)
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.attempt.factory import (
    make_attempt_orchestrator_factory,
)
from task_center.attempt import AttemptFailReason, AttemptStatus
from task_center.attempt.runtime import AttemptRuntime
from task_center.episode.episode import Episode
from task_center.task import HarnessTaskStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartedMission:
    parent_task_id: str
    # ``None`` when the caller is the attempt-less entry executor.
    parent_attempt_id: str | None
    mission_id: str
    initial_episode_id: str
    initial_attempt_id: str
    goal: str


class MissionStarter:
    """Single orchestration entry point for executor → delegated mission start."""

    def __init__(self, *, runtime: AttemptRuntime) -> None:
        self._runtime = runtime
        self._handler: MissionHandler | None = None

    def start(
        self,
        *,
        task_center_run_id: str,
        parent_task_id: str,
        parent_attempt_id: str | None,
        goal: str,
    ) -> StartedMission:
        self._assert_parent_running_and_no_open_child(
            parent_task_id=parent_task_id,
            parent_attempt_id=parent_attempt_id,
        )

        handler = self._build_handler()
        delegated_mission = handler.create_mission(
            task_center_run_id=task_center_run_id,
            requested_by_task_id=parent_task_id,
            goal=goal,
        )
        (
            initial_episode,
            episode_manager,
        ) = handler.create_initial_episode_with_manager(
            mission_id=delegated_mission.id,
        )

        initial_attempt = None
        try:
            initial_attempt = episode_manager.create_initial_attempt(start=False)
            self._mark_parent_waiting(
                parent_task_id=parent_task_id,
                parent_attempt_id=parent_attempt_id,
                mission=delegated_mission,
                episode=initial_episode,
                attempt_id=initial_attempt.id,
                goal=goal,
            )
            episode_manager.start_attempt(initial_attempt)
        except Exception:
            self._compensate_failed_start(
                mission=delegated_mission,
                episode=initial_episode,
                initial_attempt_id=(
                    initial_attempt.id if initial_attempt is not None else None
                ),
                parent_task_id=parent_task_id,
            )
            raise

        assert initial_attempt is not None
        return StartedMission(
            parent_task_id=parent_task_id,
            parent_attempt_id=parent_attempt_id,
            mission_id=delegated_mission.id,
            initial_episode_id=initial_episode.id,
            initial_attempt_id=initial_attempt.id,
            goal=goal,
        )

    # ---- internal -------------------------------------------------------

    def _build_handler(self) -> MissionHandler:
        if self._handler is not None:
            return self._handler
        manager_registry = self._runtime.manager_registry
        if manager_registry is None:
            raise TaskCenterInvariantViolation(
                "MissionStarter requires an episode manager registry."
            )
        router = MissionCloseReportRouter(runtime=self._runtime)

        def _deliver(report: MissionCloseReport) -> None:
            router.deliver(report)

        orchestrator_factory = make_attempt_orchestrator_factory(
            runtime=self._runtime,
        )
        self._handler = MissionHandler(
            mission_store=self._runtime.mission_store,
            episode_store=self._runtime.episode_store,
            attempt_store=self._runtime.attempt_store,
            manager_registry=manager_registry,
            config=self._runtime.lifecycle_config,
            deliver_close_report=_deliver,
            orchestrator_factory=orchestrator_factory,
        )
        return self._handler

    def _assert_parent_running_and_no_open_child(
        self,
        *,
        parent_task_id: str,
        parent_attempt_id: str | None,
    ) -> None:
        task = self._runtime.task_store.get_task(parent_task_id)
        if task is None:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} was not found."
            )
        if task.get("status") != HarnessTaskStatus.RUNNING.value:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} is not running; "
                "delegated mission start requires a running generator task."
            )
        attached_attempt = str(task.get("task_center_attempt_id") or "")
        # In entry mode the caller has no parent attempt (parent_attempt_id
        # is None) and the task row's attempt id column is empty/None too. In
        # attempt mode both must match.
        expected = parent_attempt_id or ""
        if attached_attempt != expected:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} is attached to attempt "
                f"{attached_attempt!r}, not {expected!r}."
            )
        # Entry-mode caveat: the entry task's *own* mission has
        # ``requested_by_task_id == entry_task_id`` because the entry task
        # is the top-level requestor. That self-mission is not a child and
        # must be excluded from the duplicate-open-child check.
        controller = self._runtime.entry_task_controller_for(parent_task_id)
        own_mission_id = (
            controller.mission_id if controller is not None else None
        )
        existing_open = [
            r
            for r in self._runtime.mission_store.list_for_executor_task(
                parent_task_id
            )
            if r.is_open and r.id != own_mission_id
        ]
        if existing_open:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} already has an open "
                f"delegated mission {existing_open[0].id!r}."
            )

    def _mark_parent_waiting(
        self,
        *,
        parent_task_id: str,
        parent_attempt_id: str | None,
        mission: Mission,
        episode: Episode,
        attempt_id: str,
        goal: str,
    ) -> None:
        # Entry-mode caller: route through the EntryTaskController so the
        # controller is the single owner of entry-task state transitions.
        controller = self._runtime.entry_task_controller_for(parent_task_id)
        if controller is not None:
            controller.mark_waiting_mission(
                delegated_mission_id=mission.id,
                delegated_episode_id=episode.id,
                delegated_attempt_id=attempt_id,
                goal=goal,
            )
            return

        summary = {
            "outcome": "mission_start",
            "summary": "Waiting on delegated mission solution.",
            "payload": {
                "mission_id": mission.id,
                "initial_episode_id": episode.id,
                "initial_attempt_id": attempt_id,
                "parent_attempt_id": parent_attempt_id,
                "goal": goal,
            },
        }
        updated = self._runtime.task_store.set_task_status_if_current(
            parent_task_id,
            expected_status=HarnessTaskStatus.RUNNING.value,
            status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
            summary=summary,
        )
        if updated is None:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} was not running when the "
                "delegated mission start tried to mark it waiting."
            )

    def _compensate_failed_start(
        self,
        *,
        mission: Mission,
        episode: Episode,
        initial_attempt_id: str | None,
        parent_task_id: str,
    ) -> None:
        """Best-effort rollback. Order: attempt → episode → mission → parent."""
        now = datetime.now(UTC)
        self._close_unstarted_attempt_after_failed_start(initial_attempt_id, now=now)
        try:
            self._runtime.episode_store.cancel_for_compensation(
                episode.id, closed_at=now
            )
        except Exception:
            logger.exception(
                "MissionStarter: cancel episode failed",
            )
        try:
            self._runtime.mission_store.cancel_for_compensation(
                mission.id, closed_at=now
            )
        except Exception:
            logger.exception(
                "MissionStarter: cancel mission failed",
            )
        try:
            controller = self._runtime.entry_task_controller_for(parent_task_id)
            if controller is not None:
                # Entry-mode rollback flows through the controller so the
                # controller stays the single owner of entry-task transitions.
                controller.restore_running_after_failed_mission_start()
            else:
                self._runtime.task_store.set_task_status_if_current(
                    parent_task_id,
                    expected_status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
                    status=HarnessTaskStatus.RUNNING.value,
                )
        except Exception:
            logger.critical(
                "MissionStarter: parent status rollback failed; "
                "task %r will remain in WAITING_COMPLEX_TASK and requires "
                "manual recovery",
                parent_task_id,
                exc_info=True,
            )
        manager_registry = self._runtime.manager_registry
        if manager_registry is not None:
            manager_registry.deregister(episode.id)

    def _close_unstarted_attempt_after_failed_start(
        self, attempt_id: str | None, *, now: datetime
    ) -> None:
        if attempt_id is None:
            return
        try:
            attempt = self._runtime.attempt_store.get(attempt_id)
            if attempt is None or attempt.is_closed:
                return
            self._runtime.attempt_store.close(
                attempt_id,
                status=AttemptStatus.FAILED,
                fail_reason=AttemptFailReason.STARTUP_FAILED,
                closed_at=now,
            )
        except Exception:
            logger.exception(
                "MissionStarter: failed to close attempt "
                "after mission-start failure",
            )
