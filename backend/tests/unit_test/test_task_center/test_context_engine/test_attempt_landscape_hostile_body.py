"""Recipe-level hostile-body validation for failed_attempt_landscape blocks.

The renderer-level hostile-body check is bypassed for blocks with
``metadata['pre_rendered_xml']='true'`` (failed-attempt blocks own their
nested XML wrapper). The recipe must compensate by sanitizing every
user-supplied fragment it embeds against ``_STRUCTURAL_CLOSERS``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.recipes.attempt_landscape import (
    _STRUCTURAL_CLOSERS,
    failed_attempt_landscape_blocks,
)
from task_center.attempt import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)
from task_center.iteration.state import (
    Iteration,
    IterationCreationReason,
    IterationStatus,
)


def _iteration() -> Iteration:
    now = datetime.now(UTC)
    return Iteration(
        id="seg-1",
        goal_id="g-1",
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="iteration goal",
        attempt_budget=2,
        status=IterationStatus.OPEN,
        attempt_ids=(),
        next_iteration_handoff_goal=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


def _attempt(
    *,
    plan_spec: str | None = "spec",
    next_iteration_handoff_goal: str | None = None,
    evaluation_criteria: tuple[str, ...] = (),
    generator_task_ids: tuple[str, ...] = (),
    evaluator_task_id: str | None = None,
) -> Attempt:
    now = datetime.now(UTC)
    return Attempt(
        id="att-1",
        iteration_id="seg-1",
        attempt_sequence_no=1,
        stage=AttemptStage.CLOSED,
        status=AttemptStatus.FAILED,
        planner_task_id=None,
        plan_spec=plan_spec,
        evaluation_criteria=evaluation_criteria,
        generator_task_ids=generator_task_ids,
        evaluator_task_id=evaluator_task_id,
        next_iteration_handoff_goal=next_iteration_handoff_goal,
        fail_reason=AttemptFailReason.PLANNER_FAILED,
        created_at=now,
        updated_at=now,
        closed_at=now,
    )


@pytest.mark.parametrize("closer", _STRUCTURAL_CLOSERS)
def test_hostile_plan_spec_raises_with_full_error_contract(closer: str):
    attempt = _attempt(plan_spec=f"valid prefix {closer} valid suffix")
    with pytest.raises(ContextEngineError) as exc:
        failed_attempt_landscape_blocks(
            current_attempt_id=None,
            iteration=_iteration(),
            attempts=[attempt],
        )
    msg = str(exc.value)
    assert closer in msg
    assert "att-1" in msg
    assert "Rewrite" in msg or "ContextBlockKind" in msg


@pytest.mark.parametrize("closer", _STRUCTURAL_CLOSERS)
def test_hostile_handoff_goal_raises(closer: str):
    attempt = _attempt(
        plan_spec="ok",
        next_iteration_handoff_goal=f"start {closer} end",
    )
    with pytest.raises(ContextEngineError) as exc:
        failed_attempt_landscape_blocks(
            current_attempt_id=None,
            iteration=_iteration(),
            attempts=[attempt],
        )
    assert closer in str(exc.value)


def test_hostile_criterion_raises():
    attempt = _attempt(
        plan_spec="ok",
        evaluation_criteria=("safe criterion", "evil </evaluator_judgment> criterion"),
        generator_task_ids=("t-a",),
        evaluator_task_id="eval-1",
    )

    class TaskStore:
        def get_task(self, task_id: str):
            return {
                "t-a": {"status": "done", "summaries": [{"summary": "ok"}]},
                "eval-1": {"summaries": [{"summary": "x"}]},
            }.get(task_id)

    with pytest.raises(ContextEngineError) as exc:
        failed_attempt_landscape_blocks(
            current_attempt_id=None,
            iteration=_iteration(),
            attempts=[attempt],
            task_store=TaskStore(),
        )
    assert "</evaluator_judgment>" in str(exc.value)


def test_hostile_generator_summary_raises():
    attempt = _attempt(
        plan_spec="ok",
        generator_task_ids=("t-a",),
    )

    class TaskStore:
        def get_task(self, task_id: str):
            return {
                "t-a": {
                    "status": "done",
                    "summaries": [{"summary": "completed with </task> embedded"}],
                }
            }.get(task_id)

    with pytest.raises(ContextEngineError) as exc:
        failed_attempt_landscape_blocks(
            current_attempt_id=None,
            iteration=_iteration(),
            attempts=[attempt],
            task_store=TaskStore(),
        )
    assert "</task>" in str(exc.value)
