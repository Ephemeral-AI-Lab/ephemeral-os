"""IterationAttemptCoordinator lifecycle tests.

Iteration close is now a primitive keyword callback
``on_iteration_closed(iteration_id=, succeeded=, deferred_goal=)`` — there is
no ``IterationClosureReport`` DTO. On a passing
close the coordinator denormalizes the passing attempt's REDUCER outcomes onto
``Iteration.outcomes`` as execution outcome records; on a failed close it
denormalizes the last failed attempt's failed-task outcomes.
"""

from __future__ import annotations

import json

import pytest

from workflow.iteration import IterationAttemptCoordinator
from workflow._core.state import (
    AttemptFailReason,
    AttemptStatus,
    IterationCreationReason,
    IterationStatus,
)
from workflow._core.primitives import reducer_task_id


def _seed_iteration(
    workflow_store, iteration_store, task_center_run_id, attempt_budget=2
) -> str:
    workflow = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g",
    )
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="g",
        attempt_budget=attempt_budget,
    )
    return iteration.id


def _make_coordinator(iter_id, iteration_store, attempt_store, task_store=None):
    captured: list[dict] = []

    def sink(*, iteration_id, succeeded, deferred_goal):
        captured.append(
            {
                "iteration_id": iteration_id,
                "succeeded": succeeded,
                "deferred_goal": deferred_goal,
            }
        )

    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=sink,
        task_store=task_store,
    )
    return coordinator, captured


class _StartedOrchestrator:
    def __init__(self, attempt_id: str, started: list[str]) -> None:
        self.attempt_id = attempt_id
        self._started = started

    def start(self) -> None:
        self._started.append(self.attempt_id)


class _FailingStartOrchestrator:
    def __init__(self, attempt_id: str) -> None:
        self.attempt_id = attempt_id

    def start(self) -> None:
        raise RuntimeError("orchestrator start failed")


class _FakeTaskStore:
    """Minimal TaskStoreProtocol surface: returns task rows by id.

    The coordinator denormalizes the passing attempt's REDUCER outcomes by
    reading ``task_store.get_task`` per ``reducer_task_id``.
    """

    def __init__(self, rows: dict[str, dict] | None = None) -> None:
        self._rows = rows or {}

    def get_task(self, task_id: str):
        return self._rows.get(task_id)


def test_initial_iteration_creates_attempt_sequence_1(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    coordinator, _ = _make_coordinator(iter_id, iteration_store, attempt_store)
    attempt = coordinator.create_attempt()
    assert attempt.attempt_sequence_no == 1
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.attempt_ids == (attempt.id,)


def test_retry_creates_attempt_in_same_iteration(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    coordinator, _ = _make_coordinator(iter_id, iteration_store, attempt_store)
    g1 = coordinator.create_attempt()
    g2 = coordinator.create_attempt(previous_attempt_id=g1.id)
    assert g2.iteration_id == iter_id
    assert g2.attempt_sequence_no == 2
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.attempt_ids == (g1.id, g2.id)


def test_passing_attempt_with_null_continuation_signals_success(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    coordinator, captured = _make_coordinator(iter_id, iteration_store, attempt_store)
    attempt = coordinator.create_attempt()
    attempt_store.close(attempt.id, status=AttemptStatus.PASSED, fail_reason=None)
    coordinator.handle_attempt_closed(attempt.id)
    assert len(captured) == 1
    assert captured[0]["succeeded"] is True
    assert captured[0]["deferred_goal"] is None
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.status == IterationStatus.SUCCEEDED


def test_close_iteration_passed_writes_reducer_outcomes(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    """At successful close the coordinator denormalizes the passing attempt's
    REDUCER tasks onto ``Iteration.outcomes`` as execution outcome records."""
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    attempt = attempt_store.insert(iteration_id=iter_id, attempt_sequence_no=1)
    iteration_store.append_attempt_id(iter_id, attempt.id)
    red_a = reducer_task_id(attempt.id, "red_a")
    red_b = reducer_task_id(attempt.id, "red_b")
    attempt_store.set_reducer_task_ids(attempt.id, [red_a, red_b])
    attempt_store.close(attempt.id, status=AttemptStatus.PASSED, fail_reason=None)
    task_store = _FakeTaskStore(
        {
            red_a: {"status": "done", "outcomes": [{"outcome": "Storage layer ok."}]},
            red_b: {"status": "done", "outcomes": [{"outcome": "Add command ok."}]},
        }
    )
    coordinator, _ = _make_coordinator(
        iter_id, iteration_store, attempt_store, task_store
    )
    coordinator.handle_attempt_closed(attempt.id)
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.outcomes is not None
    record = json.loads(iteration.outcomes)
    assert record == [
        {
            "status": "success",
            "role": "reducer",
            "task_id": red_a,
            "outcome": "Storage layer ok.",
        },
        {
            "status": "success",
            "role": "reducer",
            "task_id": red_b,
            "outcome": "Add command ok.",
        },
    ]


def test_close_iteration_passed_outcomes_empty_without_reducers(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    """A passing attempt with no reducers yields an empty JSON outcomes record."""
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    attempt = attempt_store.insert(iteration_id=iter_id, attempt_sequence_no=1)
    iteration_store.append_attempt_id(iter_id, attempt.id)
    attempt_store.close(attempt.id, status=AttemptStatus.PASSED, fail_reason=None)
    coordinator, _ = _make_coordinator(
        iter_id, iteration_store, attempt_store, _FakeTaskStore()
    )
    coordinator.handle_attempt_closed(attempt.id)
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert json.loads(iteration.outcomes) == []


def test_passing_attempt_with_continuation_signals_deferred_goal(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    coordinator, captured = _make_coordinator(iter_id, iteration_store, attempt_store)
    attempt = coordinator.create_attempt()
    attempt_store.set_deferred_goal(
        attempt.id, deferred_goal_for_next_iteration="next-goal"
    )
    attempt_store.close(attempt.id, status=AttemptStatus.PASSED, fail_reason=None)
    coordinator.handle_attempt_closed(attempt.id)
    assert len(captured) == 1
    assert captured[0]["succeeded"] is True
    assert captured[0]["deferred_goal"] == "next-goal"
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.deferred_goal_for_next_iteration == "next-goal"


def test_passing_attempt_does_not_retry(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    """Spec rule: passing attempt always closes the iteration; no second attempt."""
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    coordinator, _ = _make_coordinator(iter_id, iteration_store, attempt_store)
    attempt = coordinator.create_attempt()
    attempt_store.close(attempt.id, status=AttemptStatus.PASSED, fail_reason=None)
    coordinator.handle_attempt_closed(attempt.id)
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.attempt_ids == (attempt.id,)
    assert iteration.status == IterationStatus.SUCCEEDED


def test_failed_attempt_with_budget_creates_next_attempt(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(
        workflow_store, iteration_store, task_center_run_id, attempt_budget=2
    )
    coordinator, captured = _make_coordinator(iter_id, iteration_store, attempt_store)
    g1 = coordinator.create_attempt()
    attempt_store.close(
        g1.id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )
    coordinator.handle_attempt_closed(g1.id)
    assert captured == []  # No closure signal yet — iteration still open.
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.is_open
    assert len(iteration.attempt_ids) == 2


def test_failed_partial_plan_attempt_retries_without_propagating_continuation(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(
        workflow_store, iteration_store, task_center_run_id, attempt_budget=2
    )
    coordinator, captured = _make_coordinator(iter_id, iteration_store, attempt_store)
    g1 = coordinator.create_attempt()
    attempt_store.set_deferred_goal(
        g1.id, deferred_goal_for_next_iteration="next slice"
    )
    attempt_store.close(
        g1.id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )

    coordinator.handle_attempt_closed(g1.id)

    assert captured == []
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.is_open
    assert iteration.deferred_goal_for_next_iteration is None
    assert len(iteration.attempt_ids) == 2


def test_coordinator_starts_orchestrator_when_factory_present(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    started: list[str] = []

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        return _StartedOrchestrator(attempt.id, started)

    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=lambda **_: None,
        orchestrator_factory=factory,
    )

    attempt = coordinator.create_attempt()

    assert started == [attempt.id]


def test_initial_attempt_start_can_be_deferred(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    started: list[str] = []

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        return _StartedOrchestrator(attempt.id, started)

    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=lambda **_: None,
        orchestrator_factory=factory,
    )

    attempt = coordinator.create_attempt(start=False)
    assert started == []

    coordinator.start_attempt(attempt)

    assert started == [attempt.id]


def test_initial_start_failure_closes_inserted_attempt(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        return _FailingStartOrchestrator(attempt.id)

    captured: list[dict] = []
    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=lambda **kw: captured.append(kw),
        orchestrator_factory=factory,
    )

    with pytest.raises(RuntimeError, match="orchestrator start failed"):
        coordinator.create_attempt()

    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert len(iteration.attempt_ids) == 1
    attempt = attempt_store.get(iteration.attempt_ids[0])
    assert attempt is not None
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.fail_reason == AttemptFailReason.STARTUP_FAILED
    assert captured == []


def test_deferred_start_failure_closes_inserted_attempt(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        return _FailingStartOrchestrator(attempt.id)

    captured: list[dict] = []
    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=lambda **kw: captured.append(kw),
        orchestrator_factory=factory,
    )

    attempt = coordinator.create_attempt(start=False)

    with pytest.raises(RuntimeError, match="orchestrator start failed"):
        coordinator.start_attempt(attempt)

    latest = attempt_store.get(attempt.id)
    assert latest is not None
    assert latest.status == AttemptStatus.FAILED
    assert latest.fail_reason == AttemptFailReason.STARTUP_FAILED
    assert captured == []


def test_retry_start_failure_exhausts_budget_and_signals_failure(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    """Retry-path startup failure closes the new attempt STARTUP_FAILED and,
    when budget is exhausted, signals a failed close instead of leaving the
    iteration open."""
    iter_id = _seed_iteration(
        workflow_store, iteration_store, task_center_run_id, attempt_budget=2
    )
    started: list[str] = []

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        if attempt.attempt_sequence_no == 1:
            return _StartedOrchestrator(attempt.id, started)
        return _FailingStartOrchestrator(attempt.id)

    captured: list[dict] = []
    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=lambda **kw: captured.append(kw),
        orchestrator_factory=factory,
        task_store=_FakeTaskStore(),
    )
    first = coordinator.create_attempt()
    attempt_store.close(
        first.id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )

    coordinator.handle_attempt_closed(first.id)

    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert len(iteration.attempt_ids) == 2
    retry_attempt = attempt_store.get(iteration.attempt_ids[-1])
    assert retry_attempt is not None
    assert retry_attempt.status == AttemptStatus.FAILED
    assert retry_attempt.fail_reason == AttemptFailReason.STARTUP_FAILED
    assert iteration.status == IterationStatus.FAILED
    assert len(captured) == 1
    assert captured[0]["succeeded"] is False


def test_retry_start_failure_with_budget_remaining_creates_next_attempt(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    """When budget remains after a startup failure on retry, the coordinator
    keeps trying until a non-failing factory or budget exhaustion."""
    iter_id = _seed_iteration(
        workflow_store, iteration_store, task_center_run_id, attempt_budget=3
    )
    started: list[str] = []

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        if attempt.attempt_sequence_no == 2:
            return _FailingStartOrchestrator(attempt.id)
        return _StartedOrchestrator(attempt.id, started)

    captured: list[dict] = []
    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=lambda **kw: captured.append(kw),
        orchestrator_factory=factory,
    )
    first = coordinator.create_attempt()
    attempt_store.close(
        first.id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )

    coordinator.handle_attempt_closed(first.id)

    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert len(iteration.attempt_ids) == 3
    g2 = attempt_store.get(iteration.attempt_ids[1])
    g3 = attempt_store.get(iteration.attempt_ids[2])
    assert g2 is not None and g2.fail_reason == AttemptFailReason.STARTUP_FAILED
    assert g3 is not None and g3.status == AttemptStatus.RUNNING
    assert iteration.is_open
    assert captured == []


def test_failed_attempt_with_budget_starts_next_attempt_orchestrator(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(
        workflow_store, iteration_store, task_center_run_id, attempt_budget=2
    )
    started: list[str] = []

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        return _StartedOrchestrator(attempt.id, started)

    captured: list[dict] = []
    coordinator = IterationAttemptCoordinator(
        iteration_id=iter_id,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        on_iteration_closed=lambda **kw: captured.append(kw),
        orchestrator_factory=factory,
    )
    attempt = coordinator.create_attempt()
    attempt_store.close(
        attempt.id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )

    coordinator.handle_attempt_closed(attempt.id)

    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert started == list(iteration.attempt_ids)
    assert captured == []


def test_failed_attempt_without_budget_signals_failure_with_outcomes(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    iter_id = _seed_iteration(
        workflow_store, iteration_store, task_center_run_id, attempt_budget=2
    )
    coordinator, captured = _make_coordinator(
        iter_id, iteration_store, attempt_store, _FakeTaskStore()
    )
    g1 = coordinator.create_attempt()
    attempt_store.close(
        g1.id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )
    coordinator.handle_attempt_closed(g1.id)
    # second attempt
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    g2_id = iteration.attempt_ids[-1]
    attempt_store.close(
        g2_id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )
    coordinator.handle_attempt_closed(g2_id)
    assert len(captured) == 1
    assert captured[0]["succeeded"] is False
    iteration = iteration_store.get(iter_id)
    assert iteration is not None
    assert iteration.status == IterationStatus.FAILED
    # Failure-aware close writes the (empty here) failed-task outcomes record.
    assert iteration.outcomes is not None
    assert json.loads(iteration.outcomes) == []


def test_creating_initial_attempt_twice_raises(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    from workflow._core.primitives import TaskCenterInvariantViolation

    iter_id = _seed_iteration(workflow_store, iteration_store, task_center_run_id)
    coordinator, _ = _make_coordinator(iter_id, iteration_store, attempt_store)
    coordinator.create_attempt()
    with pytest.raises(TaskCenterInvariantViolation):
        coordinator.create_attempt()
