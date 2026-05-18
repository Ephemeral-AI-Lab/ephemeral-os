"""GoalHandler lifecycle tests covering Phase 01 exit criteria."""

from __future__ import annotations

import pytest

from task_center._core.primitives import TaskCenterLifecycleConfig
from task_center.goal.handler import GoalHandler
from task_center.iteration import IterationManagerRegistry
from task_center.goal.state import GoalStatus
from task_center.iteration.state import (
    AttemptPlanFailed,
    SuccessContinue,
    IterationClosureReport,
    TerminalSuccess,
)
from task_center.iteration.state import (
    IterationCreationReason,
    IterationStatus,
)
from task_center._core.primitives import TaskCenterInvariantViolation


@pytest.fixture
def handler(goal_store, iteration_store, attempt_store):
    return GoalHandler(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        manager_registry=IterationManagerRegistry(),
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
    )


def test_create_goal_links_executor(
    handler, goal_store, task_center_run_id
):
    """Phase 01 exit: submit_execution_handoff -> request linked to requested_by_task_id."""
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-1",
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
    handler, goal_store, task_center_run_id
):
    """Phase 01 exit: each request records created iterations in iteration_ids."""
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    refreshed = goal_store.get(req.id)
    assert refreshed is not None
    assert refreshed.iteration_ids == (seg.id,)


def test_initial_iteration_has_sequence_one_and_initial_reason(handler, task_center_run_id):
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    assert seg.sequence_no == 1
    assert seg.creation_reason == IterationCreationReason.INITIAL
    assert seg.goal == "g"
    assert seg.is_open
    assert seg.attempt_budget == 2


def test_continuation_segment_inherits_continuation_goal(
    handler, iteration_store, task_center_run_id
):
    """Phase 01 exit: continuation creates iteration N+1 with goal from previous iteration's next_iteration_handoff_goal."""
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="initial-goal",
    )
    seg1, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    # Mark predecessor SUCCEEDED with a next_iteration_handoff_goal so the invariant passes.
    iteration_store.set_iteration_handoff_goal(seg1.id, "next-goal")
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    seg1_succeeded = iteration_store.get(seg1.id)
    assert seg1_succeeded is not None

    seg2, _ = handler.create_continuation_iteration_with_manager(
        previous_iteration=seg1_succeeded
    )
    assert seg2.sequence_no == 2
    assert seg2.creation_reason == IterationCreationReason.PARTIAL_CONTINUATION
    assert seg2.goal == "next-goal"


def test_iteration_ids_holds_multiple_segments(
    handler, goal_store, iteration_store, task_center_run_id
):
    """Phase 01 exit: iteration_ids can hold multiple Iteration ids for one request."""
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g1",
    )
    seg1, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    iteration_store.set_iteration_handoff_goal(seg1.id, "g2")
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    seg1_succeeded = iteration_store.get(seg1.id)
    assert seg1_succeeded is not None
    seg2, _ = handler.create_continuation_iteration_with_manager(
        previous_iteration=seg1_succeeded
    )
    refreshed = goal_store.get(req.id)
    assert refreshed is not None
    assert refreshed.iteration_ids == (seg1.id, seg2.id)


def test_handle_iteration_closed_terminal_success_closes_request_succeeded(
    handler, goal_store, iteration_store, task_center_run_id
):
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    handler.handle_iteration_closed(
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
    handler, goal_store, task_center_run_id
):
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    handler.handle_iteration_closed(
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
    handler, goal_store, iteration_store, task_center_run_id
):
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg1, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    iteration_store.set_iteration_handoff_goal(seg1.id, "next-goal")
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    handler.handle_iteration_closed(
        IterationClosureReport(
            iteration_id=seg1.id,
            final_attempt_id="g1",
            outcome=SuccessContinue(goal="next-goal"),
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


def test_handle_iteration_closed_deregisters_manager(
    handler, task_center_run_id
):
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    # Access the registry through the handler's private attr for verification.
    reg = handler._manager_registry  # type: ignore[attr-defined]
    assert reg.get(seg.id) is not None
    handler.handle_iteration_closed(
        IterationClosureReport(
            iteration_id=seg.id,
            final_attempt_id="g1",
            outcome=TerminalSuccess(),
        )
    )
    assert reg.get(seg.id) is None


def test_continuation_segment_only_from_succeeded_predecessor_with_goal(
    handler, iteration_store, task_center_run_id
):
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg1, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)

    # Predecessor still OPEN -> invariant violation.
    with pytest.raises(TaskCenterInvariantViolation):
        handler.create_continuation_iteration_with_manager(previous_iteration=seg1)

    # Predecessor SUCCEEDED but no next_iteration_handoff_goal -> invariant violation.
    iteration_store.set_status(seg1.id, status=IterationStatus.SUCCEEDED)
    seg1_no_goal = iteration_store.get(seg1.id)
    assert seg1_no_goal is not None
    with pytest.raises(TaskCenterInvariantViolation):
        handler.create_continuation_iteration_with_manager(previous_iteration=seg1_no_goal)


def test_iteration_manager_registry_enforces_unique_per_segment(
    handler, task_center_run_id
):
    """Phase 01 spec: exactly one IterationManager active per open iteration."""
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    handler.create_initial_iteration_with_manager(goal_id=req.id)
    # Calling create_initial_iteration again should fail because the request now
    # has iteration 1 — sequence_no 1 is no longer the contiguous next.
    with pytest.raises(TaskCenterInvariantViolation):
        handler.create_initial_iteration_with_manager(goal_id=req.id)


def test_close_goal_delivers_closure_report_when_callback_set(
    goal_store, iteration_store, attempt_store, task_center_run_id
):
    delivered: list = []

    def sink(report) -> None:
        delivered.append(report)

    handler = GoalHandler(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        manager_registry=IterationManagerRegistry(),
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        deliver_closure_report=sink,
    )
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-1",
        goal="g",
    )
    handler.create_initial_iteration_with_manager(goal_id=req.id)
    handler.close_goal(
        goal_id=req.id,
        succeeded=True,
        final_iteration_id="seg",
        final_attempt_id="g1",
    )
    assert len(delivered) == 1
    assert delivered[0].outcome == "success"
    assert delivered[0].requested_by_task_id == "executor-1"


def test_handler_passes_orchestrator_factory_to_spawned_manager(
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

    registry = IterationManagerRegistry()
    handler = GoalHandler(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        manager_registry=registry,
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        orchestrator_factory=factory,
    )
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-1",
        goal="g",
    )
    iteration, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    manager = registry.get(iteration.id)
    assert manager is not None

    attempt = manager.create_initial_attempt()

    assert started == [attempt.id]


def test_no_root_creation_reason_in_lifecycle(handler, task_center_run_id):
    """Phase 01 spec: 'root' creation reason is not allowed."""
    # Indirect: handler-driven iteration creation only ever uses INITIAL or
    # PARTIAL_CONTINUATION. There is no public path that produces 'root'.
    req = handler.create_goal(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg, _ = handler.create_initial_iteration_with_manager(goal_id=req.id)
    assert seg.creation_reason in (
        IterationCreationReason.INITIAL,
        IterationCreationReason.PARTIAL_CONTINUATION,
    )
