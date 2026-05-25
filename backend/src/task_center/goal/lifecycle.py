"""Goal lifecycle coordination.

``GoalLifecycle`` is the entry point for creating a goal, extending its
iteration chain, and closing it. The small private collaborators below keep
the persistence and continuation decisions explicit without exposing another
package surface.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from task_center._core.invariants import (
    assert_predecessor_has_deferred_goal_for_next_iteration,
    assert_goal_open,
    assert_iteration_id_unique_in_goal,
    assert_iteration_sequence_contiguous,
)
from task_center._core.persistence import (
    AttemptStoreProtocol,
    GoalStoreProtocol,
    IterationStoreProtocol,
    TaskStoreProtocol,
)
from task_center._core.primitives import (
    TaskCenterInvariantViolation,
    TaskCenterLifecycleConfig,
)
from task_center.goal.state import Goal, GoalClosureReport, GoalOrigin, GoalStatus
from task_center.iteration import (
    IterationAttemptCoordinator,
    OpenIterationCoordinatorRegistry,
    OrchestratorFactory,
)
from task_center.iteration.state import (
    AttemptPlanFailed,
    Iteration,
    IterationClosureReport,
    IterationCreationReason,
    IterationStatus,
    SuccessDeferred,
    TerminalSuccess,
)

logger = logging.getLogger(__name__)


GoalClosureReportSink = Callable[[GoalClosureReport], object]


class _CloseGoal(Protocol):
    def __call__(
        self,
        *,
        goal_id: str,
        succeeded: bool,
        final_iteration_id: str,
        final_attempt_id: str | None,
    ) -> Goal: ...


class GoalLifecycle:
    """Coordinates one goal's iteration chain and closure report delivery."""

    def __init__(
        self,
        *,
        goal_store: GoalStoreProtocol,
        iteration_store: IterationStoreProtocol,
        attempt_store: AttemptStoreProtocol,
        iteration_coordinators: OpenIterationCoordinatorRegistry,
        config: TaskCenterLifecycleConfig,
        deliver_closure_report: GoalClosureReportSink | None = None,
        orchestrator_factory: OrchestratorFactory | None = None,
        task_store: TaskStoreProtocol | None = None,
    ) -> None:
        self._deliver_closure_report = deliver_closure_report
        self._repository = _GoalRepository(goal_store)
        self._iteration_factory = _GoalIterationFactory(
            goal_repository=self._repository,
            iteration_store=iteration_store,
            attempt_store=attempt_store,
            iteration_coordinators=iteration_coordinators,
            config=config,
            on_iteration_closed=self.handle_iteration_closed,
            orchestrator_factory=orchestrator_factory,
            task_store=task_store,
        )
        self._closure_router = _IterationClosureRouter(
            iteration_factory=self._iteration_factory,
            iteration_store=iteration_store,
            iteration_coordinators=iteration_coordinators,
            close_goal=self.close_goal,
        )

    def create_goal(
        self,
        *,
        task_center_run_id: str,
        origin: GoalOrigin,
        goal: str,
    ) -> Goal:
        return self._repository.create(
            task_center_run_id=task_center_run_id,
            origin=origin,
            goal=goal,
        )

    def create_initial_iteration_with_coordinator(
        self, *, goal_id: str
    ) -> tuple[Iteration, IterationAttemptCoordinator]:
        return self._iteration_factory.create_initial(goal_id=goal_id)

    def create_deferred_iteration_with_coordinator(
        self, *, previous_iteration: Iteration
    ) -> tuple[Iteration, IterationAttemptCoordinator]:
        return self._iteration_factory.create_from_deferred_goal(
            previous_iteration=previous_iteration
        )

    def handle_iteration_closed(self, report: IterationClosureReport) -> None:
        self._closure_router.route(report)

    def close_goal(
        self,
        *,
        goal_id: str,
        succeeded: bool,
        final_iteration_id: str,
        final_attempt_id: str | None,
    ) -> Goal:
        updated, report = self._repository.close(
            goal_id=goal_id,
            succeeded=succeeded,
            final_iteration_id=final_iteration_id,
            final_attempt_id=final_attempt_id,
        )
        if self._deliver_closure_report is not None:
            self._deliver_closure_report(report)
        return updated


class _GoalRepository:
    """CRUD + closure helpers for :class:`Goal` records."""

    def __init__(self, goal_store: GoalStoreProtocol) -> None:
        self._goal_store = goal_store

    def create(
        self,
        *,
        task_center_run_id: str,
        origin: GoalOrigin,
        goal: str,
    ) -> Goal:
        return self._goal_store.insert(
            task_center_run_id=task_center_run_id,
            origin=origin,
            goal=goal,
        )

    def require(self, goal_id: str) -> Goal:
        goal = self._goal_store.get(goal_id)
        if goal is None:
            raise TaskCenterInvariantViolation(f"Goal {goal_id!r} not found")
        return goal

    def append_iteration_id(self, goal: Goal, iteration_id: str) -> Goal:
        assert_iteration_id_unique_in_goal(goal, iteration_id)
        return self._goal_store.append_iteration_id(goal.id, iteration_id)

    def close(
        self,
        *,
        goal_id: str,
        succeeded: bool,
        final_iteration_id: str,
        final_attempt_id: str | None,
    ) -> tuple[Goal, GoalClosureReport]:
        """Close the goal and synthesise its :class:`GoalClosureReport`."""
        goal = self.require(goal_id)
        assert_goal_open(goal)
        report = GoalClosureReport(
            goal_id=goal_id,
            task_center_run_id=goal.task_center_run_id,
            origin_kind=goal.origin_kind,
            requested_by_task_id=goal.requested_by_task_id,
            outcome="success" if succeeded else "failed",
            final_iteration_id=final_iteration_id,
            final_attempt_id=final_attempt_id,
        )
        updated = self._goal_store.set_status(
            goal_id,
            status=GoalStatus.SUCCEEDED if succeeded else GoalStatus.FAILED,
            final_outcome=report.to_final_outcome(),
            closed_at=datetime.now(UTC),
        )
        return updated, report


class _GoalIterationFactory:
    """Creates :class:`Iteration` rows + their :class:`IterationAttemptCoordinator`."""

    def __init__(
        self,
        *,
        goal_repository: _GoalRepository,
        iteration_store: IterationStoreProtocol,
        attempt_store: AttemptStoreProtocol,
        iteration_coordinators: OpenIterationCoordinatorRegistry,
        config: TaskCenterLifecycleConfig,
        on_iteration_closed: Callable[[IterationClosureReport], None],
        orchestrator_factory: OrchestratorFactory | None = None,
        task_store: TaskStoreProtocol | None = None,
    ) -> None:
        self._goal_repository = goal_repository
        self._iteration_store = iteration_store
        self._attempt_store = attempt_store
        self._iteration_coordinators = iteration_coordinators
        self._config = config
        self._on_iteration_closed = on_iteration_closed
        self._orchestrator_factory = orchestrator_factory
        self._task_store = task_store

    @property
    def has_orchestrator_factory(self) -> bool:
        return self._orchestrator_factory is not None

    def create_initial(self, *, goal_id: str) -> tuple[Iteration, IterationAttemptCoordinator]:
        goal = self._goal_repository.require(goal_id)
        assert_goal_open(goal)
        assert_iteration_sequence_contiguous(goal, new_sequence_no=1)
        return self._insert_and_spawn(
            goal=goal,
            sequence_no=1,
            creation_reason=IterationCreationReason.INITIAL,
            iteration_goal=goal.goal,
        )

    def create_from_deferred_goal(
        self,
        *,
        previous_iteration: Iteration,
    ) -> tuple[Iteration, IterationAttemptCoordinator]:
        goal = self._goal_repository.require(previous_iteration.goal_id)
        assert_goal_open(goal)
        assert_predecessor_has_deferred_goal_for_next_iteration(previous_iteration)
        new_sequence_no = previous_iteration.sequence_no + 1
        assert_iteration_sequence_contiguous(goal, new_sequence_no=new_sequence_no)
        # predecessor invariant guarantees deferred_goal_for_next_iteration is not None.
        return self._insert_and_spawn(
            goal=goal,
            sequence_no=new_sequence_no,
            creation_reason=IterationCreationReason.DEFERRED_GOAL_CONTINUATION,
            iteration_goal=previous_iteration.deferred_goal_for_next_iteration,  # type: ignore[arg-type]
        )

    def _insert_and_spawn(
        self,
        *,
        goal: Goal,
        sequence_no: int,
        creation_reason: IterationCreationReason,
        iteration_goal: str,
    ) -> tuple[Iteration, IterationAttemptCoordinator]:
        iteration = self._iteration_store.insert(
            goal_id=goal.id,
            sequence_no=sequence_no,
            creation_reason=creation_reason,
            goal=iteration_goal,
            attempt_budget=self._config.default_attempt_budget,
        )
        self._goal_repository.append_iteration_id(goal, iteration.id)
        coordinator = IterationAttemptCoordinator(
            iteration_id=iteration.id,
            iteration_store=self._iteration_store,
            attempt_store=self._attempt_store,
            on_iteration_closed=self._on_iteration_closed,
            orchestrator_factory=self._orchestrator_factory,
            task_store=self._task_store,
        )
        self._iteration_coordinators.register(coordinator)
        return iteration, coordinator


class _IterationClosureRouter:
    """Routes :class:`IterationClosureReport` to continuation or goal close."""

    def __init__(
        self,
        *,
        iteration_factory: _GoalIterationFactory,
        iteration_store: IterationStoreProtocol,
        iteration_coordinators: OpenIterationCoordinatorRegistry,
        close_goal: _CloseGoal,
    ) -> None:
        self._iteration_factory = iteration_factory
        self._iteration_store = iteration_store
        self._iteration_coordinators = iteration_coordinators
        self._close_goal = close_goal

    def route(self, report: IterationClosureReport) -> None:
        iteration = self._iteration_store.get(report.iteration_id)
        if iteration is None:
            raise TaskCenterInvariantViolation(f"Iteration {report.iteration_id!r} not found")
        try:
            outcome = report.outcome
            if isinstance(outcome, SuccessDeferred):
                (
                    next_iteration,
                    next_coordinator,
                ) = self._iteration_factory.create_from_deferred_goal(previous_iteration=iteration)
                self._start_deferred_iteration(
                    next_iteration=next_iteration,
                    next_coordinator=next_coordinator,
                    previous_report=report,
                )
            elif isinstance(outcome, (TerminalSuccess, AttemptPlanFailed)):
                self._close_goal(
                    goal_id=iteration.goal_id,
                    succeeded=isinstance(outcome, TerminalSuccess),
                    final_iteration_id=iteration.id,
                    final_attempt_id=report.final_attempt_id,
                )
            else:  # pragma: no cover
                raise TaskCenterInvariantViolation(f"Unknown ClosureOutcome: {outcome!r}")
        finally:
            self._iteration_coordinators.deregister(iteration.id)

    def _start_deferred_iteration(
        self,
        *,
        next_iteration: Iteration,
        next_coordinator: IterationAttemptCoordinator,
        previous_report: IterationClosureReport,
    ) -> None:
        if not self._iteration_factory.has_orchestrator_factory:
            return
        try:
            next_coordinator.create_initial_attempt()
        except Exception:
            logger.exception(
                "_IterationClosureRouter: continuation attempt creation failed",
                extra={"iteration_id": next_iteration.id},
            )
            latest_iteration = self._iteration_store.get(next_iteration.id)
            failed_attempt_id = (
                latest_iteration.latest_attempt_id if latest_iteration else None
            ) or previous_report.final_attempt_id
            self._iteration_store.set_status(
                next_iteration.id,
                status=IterationStatus.CANCELLED,
                closed_at=datetime.now(UTC),
            )
            self._iteration_coordinators.deregister(next_iteration.id)
            self._close_goal(
                goal_id=next_iteration.goal_id,
                succeeded=False,
                final_iteration_id=next_iteration.id,
                final_attempt_id=failed_attempt_id,
            )


__all__ = [
    "GoalClosureReportSink",
    "GoalLifecycle",
]
