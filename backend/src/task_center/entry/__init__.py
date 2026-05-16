"""TaskCenter entry lifecycle internals. Use ``task_center`` externally."""

# ---------------------------------------------------------------------------
# Merged from controller.py
# ---------------------------------------------------------------------------

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
import uuid

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

    def apply_executor_failure(
        self, *, summary: str, reason: str, details: list[str]
    ) -> None:
        """Entry executor called ``submit_execution_failure``."""
        if not self._mark_terminal(
            status=TaskCenterTaskStatus.FAILED,
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


# ---------------------------------------------------------------------------
# Merged from sandbox_bridge.py
# ---------------------------------------------------------------------------

CreateSandboxFn = Callable[..., dict[str, Any]]
StartSandboxFn = Callable[[str], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class TaskCenterSandboxBinding:
    sandbox_id: str
    task_center_run_id: str
    owned_by_task_center: bool


def _default_create(**kwargs: Any) -> dict[str, Any]:
    import sandbox.api as sandbox_api

    return sandbox_api.create_sandbox(**kwargs)


def _default_start(sandbox_id: str) -> dict[str, Any]:
    import sandbox.api as sandbox_api

    return sandbox_api.start_sandbox(sandbox_id)


class TaskCenterSandboxBridge:
    """Prepare the sandbox binding used by one TaskCenter run."""

    def __init__(
        self,
        *,
        create_fn: CreateSandboxFn | None = None,
        start_fn: StartSandboxFn | None = None,
    ) -> None:
        self._create = create_fn
        self._start = start_fn

    def prepare_for_run(
        self,
        *,
        task_center_run_id: str,
        sandbox_id: str | None,
    ) -> TaskCenterSandboxBinding:
        explicit_id = str(sandbox_id or "").strip()
        if explicit_id:
            start = self._start or _default_start
            start(explicit_id)
            return TaskCenterSandboxBinding(
                sandbox_id=explicit_id,
                task_center_run_id=task_center_run_id,
                owned_by_task_center=False,
            )

        create = self._create or _default_create
        info = create(
            name=f"task-center-{uuid.uuid4().hex[:8]}",
            labels={
                "origin": "task_center",
                "task_center_run_id": task_center_run_id,
            },
        )
        new_id = str(info.get("id") or "").strip()
        if not new_id:
            raise RuntimeError("create_sandbox returned no id")
        return TaskCenterSandboxBinding(
            sandbox_id=new_id,
            task_center_run_id=task_center_run_id,
            owned_by_task_center=True,
        )


# ---------------------------------------------------------------------------
# Re-export coordinator symbols (must be AFTER merged content is defined)
# ---------------------------------------------------------------------------

from task_center.entry.coordinator import start_task_center_entry_run  # noqa: E402

__all__ = [
    "EntryTaskController",
    "TaskCenterSandboxBinding",
    "TaskCenterSandboxBridge",
    "start_task_center_entry_run",
]
