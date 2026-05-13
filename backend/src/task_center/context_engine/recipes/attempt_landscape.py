"""Failed attempt landscape blocks for planner context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes._summaries import latest_summary_text
from task_center.attempt import Attempt, AttemptStatus

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from db.stores.task_center_store import TaskCenterStore

MAX_FAILED_ATTEMPTS_RENDERED = 6
MAX_GENERATOR_SUMMARIES_PER_FAILED_ATTEMPT = 12
MAX_GENERATOR_SUMMARY_CHARS = 800


def failed_attempt_landscape_blocks(
    *,
    current_attempt_id: str | None,
    attempts: list[Attempt],
    task_store: TaskCenterStore | None = None,
) -> list[ContextBlock]:
    failed = sorted(
        (
            g
            for g in attempts
            if g.status == AttemptStatus.FAILED
            and g.id != current_attempt_id
        ),
        key=lambda g: g.attempt_sequence_no,
    )
    if not failed:
        return []

    if len(failed) <= MAX_FAILED_ATTEMPTS_RENDERED:
        rendered = failed
        truncated: list[Attempt] = []
    else:
        rendered = failed[-MAX_FAILED_ATTEMPTS_RENDERED:]
        truncated = failed[:-MAX_FAILED_ATTEMPTS_RENDERED]

    blocks: list[ContextBlock] = [
        ContextBlock(
            kind=ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE,
            priority=ContextPriority.HIGH,
            text=_render_failed_attempt(g, task_store=task_store),
            source_id=g.id,
            source_kind="attempt",
            metadata={
                "attempt_sequence_no": str(g.attempt_sequence_no),
                "group_heading": "# Failed Attempts",
                "subheading": f"Attempt {g.attempt_sequence_no}",
            },
        )
        for g in rendered
    ]

    if truncated:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE,
                priority=ContextPriority.MEDIUM,
                text=(
                    f"{len(truncated)} earlier failed attempts omitted "
                    f"(attempt_sequence_no "
                    f"{truncated[0].attempt_sequence_no}-"
                    f"{truncated[-1].attempt_sequence_no}). "
                    f"Most recent {MAX_FAILED_ATTEMPTS_RENDERED} attempts "
                    f"shown above."
                ),
                source_id=None,
                source_kind=None,
                metadata={
                    "truncated_count": str(len(truncated)),
                    "group_heading": "# Failed Attempts",
                    "subheading": "Earlier attempts omitted",
                },
            )
        )
    return blocks


def _render_failed_attempt(
    attempt: Attempt, *, task_store: TaskCenterStore | None
) -> str:
    criteria_block = (
        "\n".join(f"  - {c}" for c in attempt.evaluation_criteria) or "  (none)"
    )
    return (
        f"plan_kind: {_plan_kind(attempt)}\n"
        f"continuation_goal: {attempt.continuation_goal or '(none)'}\n"
        f"task_specification: {attempt.task_specification or '(missing)'}\n"
        f"evaluation_criteria:\n{criteria_block}\n"
        "generator_summaries:\n"
        f"{_render_generator_summaries(attempt, task_store=task_store)}\n"
        f"fail_reason: {attempt.fail_reason.value if attempt.fail_reason else 'unknown'}"
    )


def _render_generator_summaries(
    attempt: Attempt, *, task_store: TaskCenterStore | None
) -> str:
    if task_store is None or not attempt.generator_task_ids:
        return "  (none)"

    rendered: list[str] = []
    task_ids, omitted = _selected_generator_task_ids(attempt)
    for task_id in task_ids:
        if task_id is None:
            rendered.append(
                f"  - ({omitted} middle generator summaries omitted)"
            )
            continue
        task = task_store.get_task(task_id)
        if task is None:
            rendered.append(f"  - {task_id}: (missing task row)")
            continue
        summary = _truncate_summary(
            latest_summary_text(task.get("summaries")).strip()
        )
        indented = "\n".join(f"    {line}" for line in summary.splitlines())
        rendered.append(f"  - {task_id}:\n{indented}")
    return "\n".join(rendered)


def _plan_kind(attempt: Attempt) -> str:
    if attempt.continuation_goal:
        return "partial"
    if (
        attempt.task_specification
        or attempt.evaluation_criteria
        or attempt.generator_task_ids
    ):
        return "full"
    return "unsubmitted"


def _truncate_summary(summary: str) -> str:
    if len(summary) <= MAX_GENERATOR_SUMMARY_CHARS:
        return summary
    return (
        f"{summary[:MAX_GENERATOR_SUMMARY_CHARS].rstrip()} "
        f"... (truncated to {MAX_GENERATOR_SUMMARY_CHARS} chars)"
    )


def _selected_generator_task_ids(
    attempt: Attempt,
) -> tuple[tuple[str | None, ...], int]:
    task_ids = attempt.generator_task_ids
    if len(task_ids) <= MAX_GENERATOR_SUMMARIES_PER_FAILED_ATTEMPT:
        return task_ids, 0

    head_count = MAX_GENERATOR_SUMMARIES_PER_FAILED_ATTEMPT // 2
    tail_count = MAX_GENERATOR_SUMMARIES_PER_FAILED_ATTEMPT - head_count
    omitted = len(task_ids) - head_count - tail_count
    selected: tuple[str | None, ...] = (
        *task_ids[:head_count],
        None,
        *task_ids[-tail_count:],
    )
    return selected, omitted
