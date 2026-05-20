"""Lifecycle owner for the top-level entry executor task.

The entry executor is the user-facing root agent. It is not modeled as a
:class:`Goal` — it sits one level above and either completes directly via
``submit_execution_success`` / ``submit_execution_blocker`` or delegates to
the first goal via ``submit_execution_handoff``. :class:`EntryTaskController`
owns every state transition for that root task row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from task_center._core.persistence import TaskStoreProtocol
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center.goal.state import GoalClosureReport
from task_center.task_state import TaskCenterTaskStatus


@dataclass(frozen=True, slots=True)
class EntryTaskController:
    """Single lifecycle owner for the entry executor task."""

    task_id: str
    task_center_run_id: str
    task_store: TaskStoreProtocol

    # ---- terminal events --------------------------------------------------

    def apply_executor_success(
        self, *, summary: str, artifacts: list[str]
    ) -> None:
        """Entry executor called ``submit_execution_success``."""
        if not self._mark_terminal(
            status=TaskCenterTaskStatus.DONE,
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
        self._finish_run(status="done")

    def apply_executor_blocker(self, *, summary: str) -> None:
        """Entry executor called ``submit_execution_blocker``."""
        if not self._mark_terminal(
            status=TaskCenterTaskStatus.BLOCKED,
            summary={
                "outcome": "blocker",
                "summary": summary,
                "payload": {
                    "generator_role": "entry_executor",
                },
            },
        ):
            return
        self._finish_run(status="failed")

    def apply_run_exhausted(self, *, summary: str) -> None:
        """Launcher detected the entry agent ended without a terminal."""
        if not self._mark_terminal(
            status=TaskCenterTaskStatus.FAILED,
            summary={
                "fail_reason": "run_exhausted",
                "summary": summary,
            },
        ):
            return
        self._finish_run(status="failed")

    # ---- delegated-goal resume -----------------------------------------

    def apply_goal_closure_report(
        self, report: GoalClosureReport
    ) -> None:
        """Resume the entry task waiting on a delegated goal."""
        succeeded = report.outcome == "success"
        if succeeded:
            status = TaskCenterTaskStatus.DONE
            text = f"Delegated goal {report.goal_id} succeeded."
        else:
            status = TaskCenterTaskStatus.FAILED
            text = f"Delegated goal {report.goal_id} failed."

        try:
            updated = self.task_store.set_task_status_if_current(
                self.task_id,
                expected_status=TaskCenterTaskStatus.WAITING_GOAL.value,
                status=status.value,
                summary={
                    "outcome": report.outcome,
                    "summary": text,
                    "payload": {
                        "goal_closure_report": asdict(report),
                        "submission_kind": "goal_closure_report",
                    },
                },
            )
        except LookupError as exc:
            raise TaskCenterInvariantViolation(
                f"Entry task {self.task_id!r} not found"
            ) from exc
        if updated is None:
            return
        self._finish_run(status="done" if succeeded else "failed")

    # ---- waiting-on-delegated-goal -------------------------------------

    def mark_waiting_goal(
        self,
        *,
        delegated_goal_id: str,
        delegated_iteration_id: str,
        delegated_attempt_id: str,
        goal: str,
    ) -> None:
        """Park the entry task in ``WAITING_GOAL``."""
        summary = {
            "outcome": "goal_start",
            "summary": "Waiting on delegated goal solution.",
            "payload": {
                "goal_id": delegated_goal_id,
                "initial_iteration_id": delegated_iteration_id,
                "initial_attempt_id": delegated_attempt_id,
                "parent_attempt_id": None,
                "goal": goal,
            },
        }
        updated = self.task_store.set_task_status_if_current(
            self.task_id,
            expected_status=TaskCenterTaskStatus.RUNNING.value,
            status=TaskCenterTaskStatus.WAITING_GOAL.value,
            summary=summary,
        )
        if updated is None:
            raise TaskCenterInvariantViolation(
                f"Entry task {self.task_id!r} was not running when the "
                "delegated goal start tried to mark it waiting."
            )

    def restore_running_after_failed_goal_start(self) -> None:
        """Roll the entry task back to RUNNING after a failed goal start."""
        self.task_store.set_task_status_if_current(
            self.task_id,
            expected_status=TaskCenterTaskStatus.WAITING_GOAL.value,
            status=TaskCenterTaskStatus.RUNNING.value,
        )

    # ---- internal ----------------------------------------------------------

    def _mark_terminal(
        self,
        *,
        status: TaskCenterTaskStatus,
        summary: dict[str, Any],
    ) -> bool:
        """CAS the entry task from RUNNING to *status*."""
        try:
            updated = self.task_store.set_task_status_if_current(
                self.task_id,
                expected_status=TaskCenterTaskStatus.RUNNING.value,
                status=status.value,
                summary=summary,
            )
        except LookupError as exc:
            raise TaskCenterInvariantViolation(
                f"Entry task {self.task_id!r} not found"
            ) from exc
        return updated is not None

    def _finish_run(self, *, status: str) -> None:
        run = self.task_store.get_run(self.task_center_run_id)
        if run is None or run.get("status") in ("done", "failed"):
            return
        self.task_store.finish_run(self.task_center_run_id, status=status)


__all__ = ["EntryTaskController"]
