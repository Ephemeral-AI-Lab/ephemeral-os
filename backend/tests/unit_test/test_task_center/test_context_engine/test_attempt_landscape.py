"""Direct tests for failed attempt landscape helper behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.context_engine.packet import ContextPriority
from task_center.context_engine.recipes.attempt_landscape import (
    MAX_FAILED_ATTEMPTS_RENDERED,
    MAX_GENERATOR_SUMMARIES_PER_FAILED_ATTEMPT,
    MAX_GENERATOR_SUMMARY_CHARS,
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
    generator_task_ids: tuple[str, ...] = (),
    continuation_goal: str | None = None,
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
        generator_task_ids=generator_task_ids,
        evaluator_task_id=None,
        continuation_goal=continuation_goal,
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
    assert "plan_kind: unsubmitted" in blocks[0].text
    assert "continuation_goal: (none)" in blocks[0].text
    assert "task_specification: (missing)" in blocks[0].text
    assert "evaluation_criteria:\n  (none)" in blocks[0].text
    assert "generator_summaries:\n  (none)" in blocks[0].text
    assert "fail_reason: unknown" in blocks[0].text


def test_renders_plan_kind_continuation_goal_and_generator_summaries():
    class TaskStore:
        def get_task(self, task_id: str):
            return {
                "t-a": {
                    "summaries": [
                        {"summary": "first summary"},
                        {"summary": "built catalog slice"},
                    ]
                },
                "t-b": {"summaries": [{"outcome": "verified checkout"}]},
            }.get(task_id)

    blocks = failed_attempt_landscape_blocks(
        current_attempt_id=None,
        attempts=[
            _attempt(
                1,
                task_specification="partial spec",
                evaluation_criteria=("criterion",),
                generator_task_ids=("t-a", "t-b", "t-missing"),
                continuation_goal="continue with admin tools",
                fail_reason=AttemptFailReason.EVALUATOR_FAILED,
            )
        ],
        task_store=TaskStore(),
    )

    assert len(blocks) == 1
    assert "plan_kind: partial" in blocks[0].text
    assert "continuation_goal: continue with admin tools" in blocks[0].text
    assert "  - t-a:\n    built catalog slice" in blocks[0].text
    assert "  - t-b:\n    verified checkout" in blocks[0].text
    assert "  - t-missing: (missing task row)" in blocks[0].text


def test_renders_full_plan_kind_for_submitted_nonpartial_attempt():
    blocks = failed_attempt_landscape_blocks(
        current_attempt_id=None,
        attempts=[
            _attempt(
                1,
                task_specification="submitted spec",
                evaluation_criteria=("criterion",),
                fail_reason=AttemptFailReason.EVALUATOR_FAILED,
            )
        ],
    )

    assert "plan_kind: full" in blocks[0].text


def test_generator_summaries_are_capped_per_failed_attempt():
    class TaskStore:
        def get_task(self, task_id: str):
            return {"summaries": [{"summary": f"summary for {task_id}"}]}

    task_ids = tuple(
        f"t-{i}"
        for i in range(MAX_GENERATOR_SUMMARIES_PER_FAILED_ATTEMPT + 2)
    )

    blocks = failed_attempt_landscape_blocks(
        current_attempt_id=None,
        attempts=[
            _attempt(
                1,
                task_specification="spec",
                generator_task_ids=task_ids,
                fail_reason=AttemptFailReason.GENERATOR_FAILED,
            )
        ],
        task_store=TaskStore(),
    )

    head_count = MAX_GENERATOR_SUMMARIES_PER_FAILED_ATTEMPT // 2
    assert f"  - t-{head_count - 1}:" in blocks[0].text
    assert f"  - t-{head_count}:" not in blocks[0].text
    assert f"  - t-{len(task_ids) - 1}:" in blocks[0].text
    assert "2 middle generator summaries omitted" in blocks[0].text


def test_generator_summary_text_is_truncated():
    class TaskStore:
        def get_task(self, task_id: str):
            return {
                "summaries": [
                    {"summary": "x" * (MAX_GENERATOR_SUMMARY_CHARS + 50)}
                ]
            }

    blocks = failed_attempt_landscape_blocks(
        current_attempt_id=None,
        attempts=[
            _attempt(
                1,
                task_specification="spec",
                generator_task_ids=("t-a",),
                fail_reason=AttemptFailReason.GENERATOR_FAILED,
            )
        ],
        task_store=TaskStore(),
    )

    assert f"truncated to {MAX_GENERATOR_SUMMARY_CHARS} chars" in blocks[0].text


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
