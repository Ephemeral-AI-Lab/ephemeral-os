"""project_iteration_outcomes surfaces only the closing attempt's evidence.

Regression for the iteration-outcomes leak: a retry-then-pass iteration must not
surface successful reducer outcomes from earlier FAILED attempts. Doing so leaks
internal attempt history into the next planner's <prior_iterations> relay and
into parent generators via workflow_outcomes, contradicting the documented
``Iteration.outcomes`` contract (state.py): the passing attempt's reducer
outcomes, or on failure the last failed attempt's failed-task outcomes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from workflow._core.outcomes import ExecutionTaskOutcome, project_iteration_outcomes
from workflow.attempt import Attempt, AttemptStage, AttemptStatus


def _attempt(
    attempt_id: str,
    *,
    status: AttemptStatus,
    outcomes: tuple[ExecutionTaskOutcome, ...],
) -> Attempt:
    return Attempt(
        id=attempt_id,
        iteration_id="s1",
        attempt_sequence_no=int(attempt_id[-1]),
        stage=AttemptStage.CLOSED,
        status=status,
        planner_task_id=None,
        generator_task_ids=(),
        reducer_task_ids=(),
        deferred_goal_for_next_iteration=None,
        fail_reason=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=datetime.now(UTC),
        outcomes=outcomes,
    )


def _reducer_ok(task_id: str) -> ExecutionTaskOutcome:
    return ExecutionTaskOutcome(status="success", role="reducer", task_id=task_id, outcome="done")


def _generator_failed(task_id: str) -> ExecutionTaskOutcome:
    return ExecutionTaskOutcome(status="failed", role="generator", task_id=task_id, outcome="boom")


def test_retry_then_pass_surfaces_only_passing_attempt_reducers():
    # Attempt 1 FAILED (gen_B failed) but its red_A reducer succeeded; the retry
    # attempt 2 then PASSED. Only attempt 2's reducer evidence is canonical.
    a1 = _attempt(
        "a1",
        status=AttemptStatus.FAILED,
        outcomes=(_reducer_ok("a1:red:rA"), _generator_failed("a1:gen:gB")),
    )
    a2 = _attempt("a2", status=AttemptStatus.PASSED, outcomes=(_reducer_ok("a2:red:rA"),))

    result = project_iteration_outcomes([a1, a2], None)

    assert result == (_reducer_ok("a2:red:rA"),)


def test_failed_iteration_surfaces_only_final_attempt_failed_tasks():
    # Both attempts failed; attempt 1's successful reducer must NOT leak through.
    a1 = _attempt(
        "a1",
        status=AttemptStatus.FAILED,
        outcomes=(_reducer_ok("a1:red:rA"), _generator_failed("a1:gen:gB")),
    )
    a2 = _attempt("a2", status=AttemptStatus.FAILED, outcomes=(_generator_failed("a2:gen:gB"),))

    result = project_iteration_outcomes([a1, a2], None)

    assert result == (_generator_failed("a2:gen:gB"),)


def test_empty_attempts_projects_nothing():
    assert project_iteration_outcomes([], None) == ()
