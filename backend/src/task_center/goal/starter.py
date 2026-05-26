"""GoalStarter — single safe path from prompt text to Goal execution.

Owns origin validation, optional parent-task CAS, initial iteration/attempt
startup, and compensation on failure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from task_center.goal.close_report_router import (
    GoalClosureReportRouter,
)
from task_center.goal.lifecycle import GoalLifecycle
from task_center.goal.state import (
    GoalClosureReport,
    GoalOrigin,
    GoalOriginKind,
    Goal,
    GoalStatus,
)
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.iteration import OrchestratorFactory
from task_center.attempt.state import AttemptFailReason, AttemptStatus
from task_center.attempt.runtime import AttemptDeps
from task_center.iteration.state import Iteration, IterationStatus
from task_center.task_state import TaskCenterTaskStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartedGoal:
    origin: GoalOrigin
    parent_attempt_id: str | None
    goal_id: str
    initial_iteration_id: str
    initial_attempt_id: str
    goal: str

    @property
    def parent_task_id(self) -> str | None:
        return self.origin.task_id


class GoalStarter:
    """Single orchestration entry point for prompt → goal start."""

    def __init__(
        self,
        *,
        runtime: AttemptDeps,
        orchestrator_factory: OrchestratorFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._orchestrator_factory = orchestrator_factory or (
            lambda attempt, on_attempt_closed: AttemptOrchestrator(
                attempt=attempt,
                on_attempt_closed=on_attempt_closed,
                runtime=self._runtime,
            )
        )

    def start(self, *, prompt: str, origin: GoalOrigin) -> StartedGoal:
        prompt = prompt.strip()
        if not prompt:
            raise TaskCenterInvariantViolation("Goal prompt must be nonblank.")
        prepared = self._prepare_origin(origin)

        goal_lifecycle = self._build_goal_lifecycle()
        created_goal = goal_lifecycle.create_goal(
            task_center_run_id=prepared.task_center_run_id,
            origin=origin,
            goal=prompt,
        )
        iteration, iteration_coordinator = goal_lifecycle.create_initial_iteration_with_coordinator(
            goal_id=created_goal.id,
        )

        initial_attempt = None
        try:
            initial_attempt = iteration_coordinator.create_unstarted_initial_attempt()
            if origin.kind == GoalOriginKind.TASK:
                if prepared.parent_attempt_id is None:
                    raise TaskCenterInvariantViolation(
                        "Task-origin goal start is missing parent attempt id."
                    )
                self._mark_parent_waiting(
                    origin=origin,
                    parent_attempt_id=prepared.parent_attempt_id,
                    goal=created_goal,
                    iteration=iteration,
                    attempt_id=initial_attempt.id,
                    goal_text=prompt,
                )
            iteration_coordinator.start_attempt(initial_attempt)
        except Exception:
            self._compensate_failed_start(
                goal=created_goal,
                iteration=iteration,
                initial_attempt_id=initial_attempt.id if initial_attempt else None,
                origin=origin,
            )
            raise

        return StartedGoal(
            origin=origin,
            parent_attempt_id=prepared.parent_attempt_id,
            goal_id=created_goal.id,
            initial_iteration_id=iteration.id,
            initial_attempt_id=initial_attempt.id,
            goal=prompt,
        )

    def _prepare_origin(self, origin: GoalOrigin) -> "_PreparedGoalOrigin":
        if origin.kind == GoalOriginKind.ENTRY:
            if origin.task_center_run_id is None:
                raise TaskCenterInvariantViolation("Entry-origin goal requires task_center_run_id.")
            return _PreparedGoalOrigin(
                task_center_run_id=origin.task_center_run_id,
                parent_attempt_id=None,
            )

        if origin.task_id is None:
            raise TaskCenterInvariantViolation("Task-origin goal requires parent task_id.")
        parent_task = self._assert_parent_running_and_no_open_child(origin.task_id)
        task_center_run_id = str(parent_task.get("task_center_run_id") or "")
        if not task_center_run_id.strip():
            raise TaskCenterInvariantViolation(f"TaskCenter task {origin.task_id!r} has no run id.")
        parent_attempt_id = _parent_attempt_id(parent_task)
        if parent_attempt_id is None:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {origin.task_id!r} is not attempt-bound; "
                "task-origin goal starts require a generator task."
            )
        return _PreparedGoalOrigin(
            task_center_run_id=task_center_run_id,
            parent_attempt_id=parent_attempt_id,
        )

    def _build_goal_lifecycle(self) -> GoalLifecycle:
        iteration_coordinators = self._runtime.iteration_coordinators
        if iteration_coordinators is None:
            raise TaskCenterInvariantViolation("GoalStarter requires open iteration coordinators.")
        router = GoalClosureReportRouter(runtime=self._runtime)
        return GoalLifecycle(
            goal_store=self._runtime.goal_store,
            iteration_store=self._runtime.iteration_store,
            attempt_store=self._runtime.attempt_store,
            iteration_coordinators=iteration_coordinators,
            config=self._runtime.lifecycle_config,
            deliver_closure_report=router.deliver,
            orchestrator_factory=self._orchestrator_factory,
        )

    def _assert_parent_running_and_no_open_child(self, parent_task_id: str) -> dict[str, Any]:
        task = self._runtime.task_store.get_task(parent_task_id)
        if task is None:
            raise TaskCenterInvariantViolation(f"TaskCenter task {parent_task_id!r} was not found.")
        if task.get("status") != TaskCenterTaskStatus.RUNNING.value:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} is not running; "
                "delegated goal start requires a running generator task."
            )
        open_goals = [
            r for r in self._runtime.goal_store.list_for_parent_task(parent_task_id) if r.is_open
        ]
        if open_goals:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} already has an open "
                f"delegated goal {open_goals[0].id!r}."
            )
        return task

    def _mark_parent_waiting(
        self,
        *,
        origin: GoalOrigin,
        parent_attempt_id: str,
        goal: Goal,
        iteration: Iteration,
        attempt_id: str,
        goal_text: str,
    ) -> None:
        if origin.task_id is None:
            raise TaskCenterInvariantViolation("Task-origin goal start is missing parent task_id.")
        parent_task = self._runtime.parent_task_for_delegated_goal(
            task_id=origin.task_id, attempt_id=parent_attempt_id
        )
        if parent_task is None:
            raise TaskCenterInvariantViolation(
                f"No parent task registered for TaskCenter task "
                f"{origin.task_id!r}; goal start cannot proceed."
            )
        parent_task.mark_waiting_goal(
            delegated_goal_id=goal.id,
            delegated_iteration_id=iteration.id,
            delegated_attempt_id=attempt_id,
            goal=goal_text,
        )

    def _compensate_failed_start(
        self,
        *,
        goal: Goal,
        iteration: Iteration,
        initial_attempt_id: str | None,
        origin: GoalOrigin,
    ) -> None:
        """Best-effort rollback: attempt -> iteration -> goal -> parent.

        Each step is independent; failures are logged via ``logger.exception``
        but never block subsequent steps. If parent restore fails we route a
        synthetic failed close-report so the parent does not stay orphaned in
        ``WAITING_GOAL``.
        """
        now = datetime.now(UTC)
        runtime = self._runtime

        def _do(step_name: str, action: Callable[[], object]) -> bool:
            try:
                action()
                return True
            except Exception:
                logger.exception("GoalStart compensation step %r failed", step_name)
                return False

        _do(
            "close_unstarted_attempt",
            lambda: self._close_unstarted_attempt(initial_attempt_id, now=now),
        )
        _do(
            "cancel_iteration",
            lambda: runtime.iteration_store.set_status(
                iteration.id, status=IterationStatus.CANCELLED, closed_at=now
            ),
        )
        _do(
            "cancel_goal",
            lambda: runtime.goal_store.set_status(
                goal.id,
                status=GoalStatus.CANCELLED,
                final_outcome=None,
                closed_at=now,
            ),
        )
        if origin.kind != GoalOriginKind.TASK:
            if runtime.iteration_coordinators is not None:
                runtime.iteration_coordinators.deregister(iteration.id)
            return
        parent_task_id = origin.task_id
        if parent_task_id is None:
            return
        if not _do("restore_parent", lambda: self._restore_parent(parent_task_id)):
            _do(
                "synthetic_close_report",
                lambda: GoalClosureReportRouter(runtime=runtime).deliver(
                    GoalClosureReport(
                        goal_id=goal.id,
                        task_center_run_id=goal.task_center_run_id,
                        origin_kind=goal.origin_kind,
                        requested_by_task_id=parent_task_id,
                        outcome="failed",
                        final_iteration_id=iteration.id,
                        final_attempt_id=initial_attempt_id,
                    )
                ),
            )
        if runtime.iteration_coordinators is not None:
            runtime.iteration_coordinators.deregister(iteration.id)

    def _restore_parent(self, parent_task_id: str) -> None:
        task_row = self._runtime.task_store.get_task(parent_task_id)
        attempt_id = _parent_attempt_id(task_row) if task_row else None
        parent_task = self._runtime.parent_task_for_delegated_goal(
            task_id=parent_task_id, attempt_id=attempt_id
        )
        if parent_task is not None:
            parent_task.restore_running_after_failed_goal_start()
            return
        self._runtime.task_store.set_task_status_if_current(
            parent_task_id,
            expected_status=TaskCenterTaskStatus.WAITING_GOAL.value,
            status=TaskCenterTaskStatus.RUNNING.value,
        )

    def _close_unstarted_attempt(self, attempt_id: str | None, *, now: datetime) -> None:
        if attempt_id is None:
            return
        attempt = self._runtime.attempt_store.get(attempt_id)
        if attempt is None or attempt.is_closed:
            return
        self._runtime.attempt_store.close(
            attempt_id,
            status=AttemptStatus.FAILED,
            fail_reason=AttemptFailReason.STARTUP_FAILED,
            closed_at=now,
        )


def _parent_attempt_id(task: dict[str, Any]) -> str | None:
    raw = str(task.get("task_center_attempt_id") or "")
    return raw if raw else None


@dataclass(frozen=True, slots=True)
class _PreparedGoalOrigin:
    task_center_run_id: str
    parent_attempt_id: str | None
