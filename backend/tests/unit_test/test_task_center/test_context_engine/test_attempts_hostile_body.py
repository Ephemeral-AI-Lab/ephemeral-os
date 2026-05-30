"""Hostile-body validation for the planner failed-attempt body.

The renderer-level hostile-body check is bypassed for blocks with
``metadata['pre_rendered_xml']='true'`` (failed-attempt blocks own their nested
XML wrapper). The planner recipe must compensate by sanitizing every
user-supplied fragment it embeds against ``STRUCTURAL_CLOSERS``: the failed
plan-task ``<task>`` bodies (generators + reducers) and the ``<failure>`` line.
There is no ``<evaluator_summary>`` — the evaluator role is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)
from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.recipes._task_xml import STRUCTURAL_CLOSERS
from task_center.context_engine.recipes.planner import _render_failed_attempt_body


def _attempt(
    *,
    generator_task_ids: tuple[str, ...] = (),
    reducer_task_ids: tuple[str, ...] = (),
    fail_reason: AttemptFailReason = AttemptFailReason.TASK_FAILED,
) -> Attempt:
    now = datetime.now(UTC)
    return Attempt(
        id="att-1",
        iteration_id="seg-1",
        attempt_sequence_no=1,
        stage=AttemptStage.CLOSED,
        status=AttemptStatus.FAILED,
        planner_task_id=None,
        generator_task_ids=generator_task_ids,
        reducer_task_ids=reducer_task_ids,
        deferred_goal_for_next_iteration=None,
        fail_reason=fail_reason,
        created_at=now,
        updated_at=now,
        closed_at=now,
    )


@pytest.mark.parametrize("closer", STRUCTURAL_CLOSERS)
def test_hostile_generator_outcome_raises_with_full_error_contract(closer: str):
    """A structural closer in a terminal generator outcome (a ``<task>`` body)
    raises with the offending closer + the source id + a remediation hint."""
    attempt = _attempt(generator_task_ids=("att-1:gen:t-a",))

    class TaskStore:
        def get_task(self, task_id: str):
            return {
                "att-1:gen:t-a": {
                    "status": "done",
                    "outcomes": [{"outcome": f"valid prefix {closer} valid suffix"}],
                }
            }.get(task_id)

    with pytest.raises(ContextEngineError) as exc:
        _render_failed_attempt_body(attempt, task_store=TaskStore())
    msg = str(exc.value)
    assert closer in msg
    assert "att-1" in msg
    assert "Rewrite" in msg or "ContextBlockKind" in msg


def test_hostile_reducer_outcome_raises():
    """A structural closer in a terminal reducer outcome raises."""
    attempt = _attempt(reducer_task_ids=("att-1:red:r-a",))

    class TaskStore:
        def get_task(self, task_id: str):
            return {
                "att-1:red:r-a": {
                    "status": "failed",
                    "outcomes": [{"outcome": "evil </needs> commentary"}],
                }
            }.get(task_id)

    with pytest.raises(ContextEngineError) as exc:
        _render_failed_attempt_body(attempt, task_store=TaskStore())
    assert "</needs>" in str(exc.value)


def test_hostile_failure_line_raises():
    """A structural closer reaching the ``<failure>`` line raises.

    A blocked generator with a hostile outcome text feeds the TASK_FAILED
    failure line; the closer is rejected before it can tear the wrapper.
    """
    attempt = _attempt(generator_task_ids=("att-1:gen:t-a",))

    class TaskStore:
        def get_task(self, task_id: str):
            return {
                "att-1:gen:t-a": {
                    "status": "blocked",
                    "outcomes": [{"outcome": "blocked </failure> oops"}],
                }
            }.get(task_id)

    with pytest.raises(ContextEngineError) as exc:
        _render_failed_attempt_body(attempt, task_store=TaskStore())
    assert "</failure>" in str(exc.value)
