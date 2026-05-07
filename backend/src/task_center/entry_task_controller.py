"""EntryTaskController — lifecycle controller for the attempt-less entry executor.

Receives the lifecycle events that a :class:`AttemptOrchestrator` would
handle in attempt mode (terminal submissions, run exhaustion, delegated
complex-task close reports). The entry executor lives in a
:class:`Episode` with **zero** ``Attempt`` rows (per phase-06
*Sources of truth*: an entry episode may have zero ``Attempt`` rows);
this controller is the single owner of:

    - entry-task status transitions (RUNNING ↔ WAITING_COMPLEX_TASK ↔ DONE/FAILED)
    - entry-episode close (no attempt rows to drive the manager retry path)
    - entry-request close via :class:`MissionHandler`
    - run finalization via the handler's ``deliver_close_report`` callback

Construction is owned by :class:`TaskCenterEntryCoordinator`; the controller
is attached to :class:`AttemptRuntime.entry_task_controller` so the
close-report router and launcher can dispatch into it without further
plumbing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from db.stores.task_center_store import TaskCenterStore
from db.stores.episode_store import EpisodeStore
from task_center.mission.handler import MissionHandler
from task_center.mission.mission import MissionCloseReport
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.episode.episode import EpisodeStatus
from task_center.task import HarnessTaskStatus


@dataclass(frozen=True, slots=True)
class EntryTaskController:
    """Single lifecycle owner for the attempt-less entry executor task."""

    task_id: str
    task_center_run_id: str
    mission_id: str
    episode_id: str
    task_store: TaskCenterStore
    episode_store: EpisodeStore
    mission_handler: MissionHandler
    manager_registry: EpisodeManagerRegistry

    # ---- terminal events --------------------------------------------------

    def apply_executor_success(
        self, *, summary: str, artifacts: list[str]
    ) -> None:
        """Entry executor called ``submit_execution_success``.

        Marks the entry task DONE, closes the entry episode as succeeded,
        and closes the entry request — which in turn finalizes the run via
        the handler's ``deliver_close_report`` callback.
        """
        if not self._mark_terminal(
            status=HarnessTaskStatus.DONE,
            summary={
                "outcome": "success",
                "summary": summary,
                "payload": {
                    "generator_role": "entry_executor",
                    "artifacts": artifacts,
                },
            },
        ):
            return
        self._close_episode_and_request_succeeded(
            task_specification=summary,
            task_summary=summary,
        )

    def apply_executor_failure(
        self, *, summary: str, reason: str, details: list[str]
    ) -> None:
        """Entry executor called ``submit_execution_failure``."""
        if not self._mark_terminal(
            status=HarnessTaskStatus.FAILED,
            summary={
                "outcome": "failure",
                "summary": summary,
                "payload": {
                    "generator_role": "entry_executor",
                    "reason": reason,
                    "details": details,
                },
            },
        ):
            return
        self._close_episode_and_request_failed()

    def apply_run_exhausted(self, *, summary: str) -> None:
        """Launcher detected the entry agent ended without a terminal."""
        if not self._mark_terminal(
            status=HarnessTaskStatus.FAILED,
            summary={
                "fail_reason": "run_exhausted",
                "summary": summary,
            },
        ):
            return
        self._close_episode_and_request_failed()

    # ---- delegated-complex-task resume ------------------------------------

    def apply_mission_close_report(
        self, report: MissionCloseReport
    ) -> None:
        """Resume the entry task waiting on a delegated mission.

        Idempotent: the CAS with ``expected_status=WAITING_COMPLEX_TASK``
        returns ``None`` when the entry task has already moved off (earlier
        delivery, terminal already fired) — no pre-read needed.
        """
        succeeded = report.outcome == "success"
        if succeeded:
            status = HarnessTaskStatus.DONE
            text = (
                f"Delegated mission {report.mission_id} "
                "succeeded."
            )
        else:
            status = HarnessTaskStatus.FAILED
            text = (
                f"Delegated mission {report.mission_id} "
                "failed."
            )

        try:
            updated = self.task_store.set_task_status_if_current(
                self.task_id,
                expected_status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
                status=status.value,
                summary={
                    "outcome": report.outcome,
                    "summary": text,
                    "payload": {
                        "mission_close_report": asdict(report),
                        "submission_kind": "mission_close_report",
                    },
                },
            )
        except LookupError as exc:
            raise TaskCenterInvariantViolation(
                f"Entry task {self.task_id!r} not found"
            ) from exc
        if updated is None:
            return  # CAS miss: already delivered or already terminal.
        if succeeded:
            self._close_episode_and_request_succeeded(
                task_specification=text,
                task_summary=text,
            )
        else:
            self._close_episode_and_request_failed()

    # ---- waiting-on-delegated-mission -------------------------------------

    def mark_waiting_mission(
        self,
        *,
        delegated_mission_id: str,
        delegated_episode_id: str,
        delegated_attempt_id: str,
        goal: str,
    ) -> None:
        """Park the entry task in ``WAITING_COMPLEX_TASK``.

        Called by the mission starter when the entry executor invokes
        ``request_mission_solution``.
        """
        summary = {
            "outcome": "mission_start",
            "summary": "Waiting on delegated mission solution.",
            "payload": {
                "mission_id": delegated_mission_id,
                "initial_episode_id": delegated_episode_id,
                "initial_attempt_id": delegated_attempt_id,
                "parent_attempt_id": None,
                "goal": goal,
            },
        }
        updated = self.task_store.set_task_status_if_current(
            self.task_id,
            expected_status=HarnessTaskStatus.RUNNING.value,
            status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
            summary=summary,
        )
        if updated is None:
            raise TaskCenterInvariantViolation(
                f"Entry task {self.task_id!r} was not running when the "
                "delegated mission start tried to mark it waiting."
            )

    def restore_running_after_failed_mission_start(self) -> None:
        """Roll the entry task back to RUNNING after a failed mission start.

        Mirror image of :meth:`mark_waiting_mission` for compensation.
        """
        self.task_store.set_task_status_if_current(
            self.task_id,
            expected_status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
            status=HarnessTaskStatus.RUNNING.value,
        )

    # ---- internal ----------------------------------------------------------

    def _mark_terminal(
        self,
        *,
        status: HarnessTaskStatus,
        summary: dict[str, Any],
    ) -> bool:
        """CAS the entry task from RUNNING to *status*.

        Returns ``True`` when the transition happened, ``False`` when the
        task was already off RUNNING (terminal raced ahead, or the entry
        was parked in WAITING_COMPLEX_TASK and resumed via close-report).
        Idempotent at the CAS level — no pre-read needed.
        """
        try:
            updated = self.task_store.set_task_status_if_current(
                self.task_id,
                expected_status=HarnessTaskStatus.RUNNING.value,
                status=status.value,
                summary=summary,
            )
        except LookupError as exc:
            raise TaskCenterInvariantViolation(
                f"Entry task {self.task_id!r} not found"
            ) from exc
        return updated is not None

    def _close_episode_and_request_succeeded(
        self,
        *,
        task_specification: str,
        task_summary: str,
    ) -> None:
        """Close the entry episode + entry mission as succeeded.

        Closing the mission triggers ``deliver_close_report`` (wired by the
        entry coordinator) which finishes the run.
        """
        self._close_entry_segment_succeeded(
            task_specification=task_specification,
            task_summary=task_summary,
        )
        self.manager_registry.deregister(self.episode_id)
        self.mission_handler.close_mission(
            mission_id=self.mission_id,
            succeeded=True,
            final_episode_id=self.episode_id,
            final_attempt_id=None,
        )

    def _close_episode_and_request_failed(self) -> None:
        """Close the entry episode + entry mission as failed."""
        self._close_entry_segment_failed()
        self.manager_registry.deregister(self.episode_id)
        self.mission_handler.close_mission(
            mission_id=self.mission_id,
            succeeded=False,
            final_episode_id=self.episode_id,
            final_attempt_id=None,
        )

    def _close_entry_segment_succeeded(
        self,
        *,
        task_specification: str,
        task_summary: str,
    ) -> None:
        """Atomically close the entry episode as succeeded.

        Idempotent: if the episode is already closed, no-op.
        """
        episode = self.episode_store.get(self.episode_id)
        if episode is None:
            raise TaskCenterInvariantViolation(
                f"Entry episode {self.episode_id!r} not found"
            )
        if episode.status != EpisodeStatus.OPEN:
            return
        self.episode_store.close_succeeded(
            self.episode_id,
            task_specification=task_specification,
            task_summary=task_summary,
            closed_at=datetime.now(UTC),
        )

    def _close_entry_segment_failed(self) -> None:
        """Atomically close the entry episode as failed."""
        episode = self.episode_store.get(self.episode_id)
        if episode is None:
            raise TaskCenterInvariantViolation(
                f"Entry episode {self.episode_id!r} not found"
            )
        if episode.status != EpisodeStatus.OPEN:
            return
        self.episode_store.set_status(
            self.episode_id,
            status=EpisodeStatus.FAILED,
            closed_at=datetime.now(UTC),
        )
