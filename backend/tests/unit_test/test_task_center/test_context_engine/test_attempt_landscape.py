"""Direct tests for failed attempt landscape helper behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.context_engine.packet import ContextPriority
from task_center.context_engine.recipes.attempt_landscape import (
    MAX_FAILED_ATTEMPTS_RENDERED,
    failed_attempt_landscape_blocks,
)
from task_center.attempt import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)


def _attempt(
    sequence_no: int,
    *,
    attempt_id: str | None = None,
    status: AttemptStatus = AttemptStatus.FAILED,
    task_specification: str | None = None,
    evaluation_criteria: tuple[str, ...] = (),
    fail_reason: AttemptFailReason | None = None,
) -> Attempt:
    now = datetime.now(UTC)
    return Attempt(
        id=attempt_id or f"attempt-{sequence_no}",
        episode_id="seg-1",
        attempt_sequence_no=sequence_no,
        stage=AttemptStage.CLOSED,
        status=status,
        planner_task_id=None,
        task_specification=task_specification,
        evaluation_criteria=evaluation_criteria,
        generator_task_ids=(),
        evaluator_task_id=None,
        continuation_goal=None,
        fail_reason=fail_reason,
        created_at=now,
        updated_at=now,
        closed_at=now,
    )


def test_excludes_current_attempt_even_if_current_is_failed():
    current = _attempt(
        3,
        attempt_id="current",
        task_specification="current spec",
        evaluation_criteria=("current crit",),
        fail_reason=AttemptFailReason.PLANNER_FAILED,
    )
    blocks = failed_attempt_landscape_blocks(
        current_attempt_id=current.id,
        attempts=[
            current,
            _attempt(
                2,
                task_specification="older spec",
                evaluation_criteria=("older crit",),
                fail_reason=AttemptFailReason.GENERATOR_FAILED,
            ),
            _attempt(4, status=AttemptStatus.RUNNING),
            _attempt(
                1,
                task_specification="oldest spec",
                evaluation_criteria=("oldest crit",),
                fail_reason=AttemptFailReason.EVALUATOR_FAILED,
            ),
        ],
    )

    assert [block.source_id for block in blocks] == ["attempt-1", "attempt-2"]
    assert all(block.priority == ContextPriority.HIGH for block in blocks)


def test_renders_missing_spec_empty_criteria_and_unknown_reason():
    blocks = failed_attempt_landscape_blocks(
        current_attempt_id=None,
        attempts=[_attempt(1)],
    )

    assert len(blocks) == 1
    assert "task_specification: (missing)" in blocks[0].text
    assert "evaluation_criteria:\n  (none)" in blocks[0].text
    assert "fail_reason: unknown" in blocks[0].text


def test_truncation_keeps_most_recent_failed_attempts_and_reports_omitted_range():
    blocks = failed_attempt_landscape_blocks(
        current_attempt_id=None,
        attempts=[
            _attempt(
                sequence_no,
                task_specification=f"spec-{sequence_no}",
                evaluation_criteria=(f"crit-{sequence_no}",),
                fail_reason=AttemptFailReason.GENERATOR_FAILED,
            )
            for sequence_no in range(MAX_FAILED_ATTEMPTS_RENDERED + 2, 0, -1)
        ],
    )

    rendered = blocks[:-1]
    truncation = blocks[-1]

    assert [block.metadata["attempt_sequence_no"] for block in rendered] == [
        str(sequence_no)
        for sequence_no in range(
            3, MAX_FAILED_ATTEMPTS_RENDERED + 3
        )
    ]
    assert all(block.priority == ContextPriority.HIGH for block in rendered)
    assert truncation.priority == ContextPriority.MEDIUM
    assert truncation.metadata["truncated_count"] == "2"
    assert "attempt_sequence_no 1-2" in truncation.text
