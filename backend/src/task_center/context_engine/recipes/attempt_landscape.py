"""Failed attempt landscape blocks for planner context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes._summaries import latest_summary_text
from task_center.attempt.state import Attempt, AttemptFailReason, AttemptStatus

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from db.stores.task_center_store import TaskCenterStore


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

    return [
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
        for g in failed
    ]


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
        f"fail_reason: {_render_fail_reason(attempt, task_store=task_store)}"
    )


def _render_generator_summaries(
    attempt: Attempt, *, task_store: TaskCenterStore | None
) -> str:
    if task_store is None or not attempt.generator_task_ids:
        return "  (none)"

    rendered: list[str] = []
    for task_id in attempt.generator_task_ids:
        task = task_store.get_task(task_id)
        if task is None:
            rendered.append(f"  - {task_id}: (missing task row)")
            continue
        summary = latest_summary_text(task.get("summaries")).strip()
        indented = "\n".join(f"    {line}" for line in summary.splitlines())
        rendered.append(f"  - {task_id}:\n{indented}")
    return "\n".join(rendered)


def _render_fail_reason(
    attempt: Attempt, *, task_store: TaskCenterStore | None
) -> str:
    reason = attempt.fail_reason.value if attempt.fail_reason else "unknown"
    if (
        task_store is None
        or attempt.fail_reason != AttemptFailReason.EVALUATOR_FAILED
        or not attempt.evaluator_task_id
    ):
        return reason

    task = task_store.get_task(attempt.evaluator_task_id)
    summaries = task.get("summaries") if task is not None else None
    if not summaries:
        return reason

    summary = " ".join(
        line.strip()
        for line in latest_summary_text(summaries).splitlines()
        if line.strip()
    )
    if not summary:
        return reason
    return f"{reason}: {summary}"


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
