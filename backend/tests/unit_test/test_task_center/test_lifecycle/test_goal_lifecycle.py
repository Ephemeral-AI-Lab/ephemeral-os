"""GoalLifecycle lifecycle tests covering Phase 01 exit criteria."""

from __future__ import annotations

import pytest

from task_center._core.primitives import TaskCenterLifecycleConfig
from task_center.goal.lifecycle import GoalLifecycle
from task_center.iteration import OpenIterationCoordinatorRegistry
from task_center.goal.state import GoalOrigin, GoalStatus
from task_center.iteration.state import (
    AttemptPlanFailed,
    SuccessDeferred,
    IterationClosureReport,
    TerminalSuccess,
)
from task_center.iteration.state import (
    IterationCreationReason,
    IterationStatus,
)
from task_center._core.primitives import TaskCenterInvariantViolation


@pytest.fixture
def iteration_coordinators():
    return OpenIterationCoordinatorRegistry()


@pytest.fixture
def goal_lifecycle(goal_store, iteration_store, attempt_store, iteration_coordinators):
    return GoalLifecycle(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=iteration_coordinators,
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
    )


def test_create_goal_links_executor(
    goal_lifecycle, goal_store, task_center_run_id
):
    """Phase 01 exit: submit_execution_handoff -> request linked to requested_by_task_id."""
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="executor-1"),
        goal="solve X",
    )
    assert req.requested_by_task_id == "executor-1"
    assert req.task_center_run_id == task_center_run_id
    assert req.is_open
    assert req.iteration_ids == ()
    persisted = goal_store.get(req.id)
    assert persisted is not None
    assert persisted.requested_by_task_id == "executor-1"


def test_request_records_segments_in_iteration_ids(
    goal_lifecycle, goal_store, task_center_run_id
):
    """Phase 01 exit: each request records created iterations in iteration_ids."""
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    refreshed = goal_store.get(req.id)
    assert refreshed is not None
    assert refreshed.iteration_ids == (seg.id,)


def test_initial_iteration_has_sequence_one_and_initial_reason(goal_lifecycle, task_center_run_id):
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    assert seg.sequence_no == 1
    assert seg.creation_reason == IterationCreationReason.INITIAL
    assert seg.goal == "g"
    assert seg.is_open
    assert seg.attempt_budget == 2


def test_continuation_segment_inherits_deferred_goal(
    goal_lifecycle, iteration_store, task_center_run_id
):
    """Phase 01 exit: continuation creates iteration N+1 with goal from previous iteration's deferred_goal_for_next_iteration."""
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="initial-goal",
    )
    seg1, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    # Mark predecessor SUCCEEDED with a deferred_goal_for_next_iteration so the invariant passes.
    iteration_store.set_deferred_goal_for_next_iteration(seg1.id, "next-goal")
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    seg1_succeeded = iteration_store.get(seg1.id)
    assert seg1_succeeded is not None

    seg2, _ = goal_lifecycle.create_deferred_iteration_with_coordinator(
        previous_iteration=seg1_succeeded
    )
    assert seg2.sequence_no == 2
    assert seg2.creation_reason == IterationCreationReason.DEFERRED_GOAL_CONTINUATION
    assert seg2.goal == "next-goal"


def test_iteration_ids_holds_multiple_segments(
    goal_lifecycle, goal_store, iteration_store, task_center_run_id
):
    """Phase 01 exit: iteration_ids can hold multiple Iteration ids for one request."""
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g1",
    )
    seg1, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    iteration_store.set_deferred_goal_for_next_iteration(seg1.id, "g2")
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    seg1_succeeded = iteration_store.get(seg1.id)
    assert seg1_succeeded is not None
    seg2, _ = goal_lifecycle.create_deferred_iteration_with_coordinator(
        previous_iteration=seg1_succeeded
    )
    refreshed = goal_store.get(req.id)
    assert refreshed is not None
    assert refreshed.iteration_ids == (seg1.id, seg2.id)


def test_handle_iteration_closed_terminal_success_closes_request_succeeded(
    goal_lifecycle, goal_store, iteration_store, task_center_run_id
):
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    goal_lifecycle.handle_iteration_closed(
        IterationClosureReport(
            iteration_id=seg.id,
            final_attempt_id="g1",
            outcome=TerminalSuccess(),
        )
    )
    final = goal_store.get(req.id)
    assert final is not None
    assert final.status == GoalStatus.SUCCEEDED
    assert final.final_outcome == {
        "outcome": "success",
        "final_iteration_id": seg.id,
        "final_attempt_id": "g1",
    }


def test_handle_iteration_closed_attempt_plan_failed_closes_request_failed(
    goal_lifecycle, goal_store, task_center_run_id
):
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    goal_lifecycle.handle_iteration_closed(
        IterationClosureReport(
            iteration_id=seg.id,
            final_attempt_id="g1",
            outcome=AttemptPlanFailed(
                failure_summary="boom", prior_attempt_history=()
            ),
        )
    )
    final = goal_store.get(req.id)
    assert final is not None
    assert final.status == GoalStatus.FAILED


def test_handle_iteration_closed_success_continue_creates_continuation(
    goal_lifecycle, goal_store, iteration_store, task_center_run_id
):
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg1, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    iteration_store.set_deferred_goal_for_next_iteration(seg1.id, "next-goal")
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    goal_lifecycle.handle_iteration_closed(
        IterationClosureReport(
            iteration_id=seg1.id,
            final_attempt_id="g1",
            outcome=SuccessDeferred(deferred_goal_for_next_iteration="next-goal"),
        )
    )
    refreshed = goal_store.get(req.id)
    assert refreshed is not None
    assert len(refreshed.iteration_ids) == 2
    seg2_id = refreshed.iteration_ids[1]
    seg2 = iteration_store.get(seg2_id)
    assert seg2 is not None
    assert seg2.sequence_no == 2
    assert seg2.goal == "next-goal"


def test_handle_iteration_closed_deregisters_coordinator(
    goal_lifecycle, iteration_coordinators, task_center_run_id
):
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    assert iteration_coordinators.get(seg.id) is not None
    goal_lifecycle.handle_iteration_closed(
        IterationClosureReport(
            iteration_id=seg.id,
            final_attempt_id="g1",
            outcome=TerminalSuccess(),
        )
    )
    assert iteration_coordinators.get(seg.id) is None


def test_continuation_segment_only_from_succeeded_predecessor_with_goal(
    goal_lifecycle, iteration_store, task_center_run_id
):
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg1, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)

    # Predecessor still OPEN -> invariant violation.
    with pytest.raises(TaskCenterInvariantViolation):
        goal_lifecycle.create_deferred_iteration_with_coordinator(previous_iteration=seg1)

    # Predecessor SUCCEEDED but no deferred_goal_for_next_iteration -> invariant violation.
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    seg1_no_goal = iteration_store.get(seg1.id)
    assert seg1_no_goal is not None
    with pytest.raises(TaskCenterInvariantViolation):
        goal_lifecycle.create_deferred_iteration_with_coordinator(previous_iteration=seg1_no_goal)


def test_open_iteration_coordinators_enforces_unique_per_iteration(
    goal_lifecycle, task_center_run_id
):
    """Phase 01 spec: exactly one IterationAttemptCoordinator active per open iteration."""
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    # Calling create_initial_iteration again should fail because the request now
    # has iteration 1 — sequence_no 1 is no longer the contiguous next.
    with pytest.raises(TaskCenterInvariantViolation):
        goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)


def test_close_goal_delivers_closure_report_when_callback_set(
    goal_store, iteration_store, attempt_store, task_center_run_id
):
    delivered: list = []

    def sink(report) -> None:
        delivered.append(report)

    goal_lifecycle = GoalLifecycle(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        deliver_closure_report=sink,
    )
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="executor-1"),
        goal="g",
    )
    goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    goal_lifecycle.close_goal(
        goal_id=req.id,
        succeeded=True,
        final_iteration_id="seg",
        final_attempt_id="g1",
    )
    assert len(delivered) == 1
    assert delivered[0].outcome == "success"
    assert delivered[0].requested_by_task_id == "executor-1"


def test_goal_lifecycle_passes_orchestrator_factory_to_spawned_coordinator(
    goal_store, iteration_store, attempt_store, task_center_run_id
):
    started: list[str] = []

    class _StartedOrchestrator:
        def __init__(self, attempt_id: str) -> None:
            self.attempt_id = attempt_id

        def start(self) -> None:
            started.append(self.attempt_id)

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        return _StartedOrchestrator(attempt.id)

    registry = OpenIterationCoordinatorRegistry()
    goal_lifecycle = GoalLifecycle(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=registry,
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        orchestrator_factory=factory,
    )
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="executor-1"),
        goal="g",
    )
    iteration, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    coordinator = registry.get(iteration.id)
    assert coordinator is not None

    attempt = coordinator.create_initial_attempt()

    assert started == [attempt.id]


def test_no_legacy_entry_creation_reason_in_lifecycle(goal_lifecycle, task_center_run_id):
    """Phase 01 spec: no special entry creation reason is allowed."""
    # Indirect: goal-lifecycle driven iteration creation only ever uses INITIAL or
    # DEFERRED_GOAL_CONTINUATION. There is no public path that produces a
    # special entry-only iteration reason.
    req = goal_lifecycle.create_goal(
        task_center_run_id=task_center_run_id,
        origin=GoalOrigin.task(task_id="t1"),
        goal="g",
    )
    seg, _ = goal_lifecycle.create_initial_iteration_with_coordinator(goal_id=req.id)
    assert seg.creation_reason in (
        IterationCreationReason.INITIAL,
        IterationCreationReason.DEFERRED_GOAL_CONTINUATION,
    )
