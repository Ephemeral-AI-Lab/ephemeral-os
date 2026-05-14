"""Failed attempt landscape blocks for planner context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes._shared import latest_summary_text
from task_center.attempt.state import Attempt, AttemptStatus

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import TaskStoreProtocol


_MISSING_TASK_ROW_STATUS = "missing task row"
_PREMATURE_STATUSES = frozenset({"failed", "blocked", _MISSING_TASK_ROW_STATUS})
_EMPTY_SUMMARY_PLACEHOLDERS = frozenset({"(empty)", "(no summary recorded)"})


@dataclass(frozen=True, slots=True)
class _GeneratorOutcome:
    task_id: str
    status: str
    blocked_by: str | None
    summary: str | None


def failed_attempt_landscape_blocks(
    *,
    current_attempt_id: str | None,
    attempts: list[Attempt],
    task_store: TaskStoreProtocol | None = None,
) -> list[ContextBlock]:
    failed = sorted(
        (
            a
            for a in attempts
            if a.status == AttemptStatus.FAILED and a.id != current_attempt_id
        ),
        key=lambda a: a.attempt_sequence_no,
    )
    return [
        ContextBlock(
            kind=ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE,
            priority=ContextPriority.HIGH,
            text=_render_failed_attempt(a, task_store=task_store),
            source_id=a.id,
            source_kind="attempt",
            metadata={
                "attempt_sequence_no": str(a.attempt_sequence_no),
                "group_heading": "# Prior Failed Attempts",
                "subheading": f"Attempt {a.attempt_sequence_no}",
            },
        )
        for a in failed
    ]


def _render_failed_attempt(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    outcomes = _generator_outcomes(attempt, task_store=task_store)

    if attempt.continuation_goal:
        plan_kind = "partial"
    elif (
        attempt.task_specification
        or attempt.evaluation_criteria
        or attempt.generator_task_ids
    ):
        plan_kind = "full"
    else:
        plan_kind = "unsubmitted"

    sections = [
        "### Accepted Plan\n\n"
        f"Plan type: {plan_kind}\n\n"
        f"Specification:\n{attempt.task_specification or '(not submitted)'}",
        _render_generator_outcomes(outcomes),
    ]

    has_premature = any(o.status in _PREMATURE_STATUSES for o in outcomes)
    if not has_premature and task_store and attempt.evaluator_task_id is not None:
        evaluator_task = task_store.get_task(attempt.evaluator_task_id)
        evaluator_summary = (
            "(missing evaluator task row)"
            if evaluator_task is None
            else latest_summary_text(evaluator_task.get("summaries"))
        )
        criteria_block = (
            "\n".join(f"  - {c}" for c in attempt.evaluation_criteria) or "  (none)"
        )
        sections.append(
            "### Evaluator Judgment\n\n"
            f"Evaluation criteria:\n{criteria_block}\n\n"
            f"Evaluator summary:\n{evaluator_summary}"
        )
    return "\n\n".join(sections)


def _render_generator_outcomes(outcomes: list[_GeneratorOutcome]) -> str:
    if not outcomes:
        status_lines = ["- (no generator tasks recorded)"]
    else:
        status_lines = [
            f"- {o.task_id}: {o.status} by {o.blocked_by}"
            if o.blocked_by
            else f"- {o.task_id}: {o.status}"
            for o in outcomes
        ]
    body = "### Generator Outcomes\n\nStatus summary:\n" + "\n".join(status_lines)

    details = [
        f"#### {o.task_id}\n\n{o.summary}"
        for o in outcomes
        if o.summary and o.summary not in _EMPTY_SUMMARY_PLACEHOLDERS
    ]
    if details:
        body += "\n\n" + "\n\n".join(details)
    return body


def _generator_outcomes(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> list[_GeneratorOutcome]:
    if task_store is None or not attempt.generator_task_ids:
        return []

    outcomes: list[_GeneratorOutcome] = []
    for task_id in attempt.generator_task_ids:
        task = task_store.get_task(task_id)
        if task is None:
            outcomes.append(
                _GeneratorOutcome(
                    task_id=task_id,
                    status=_MISSING_TASK_ROW_STATUS,
                    blocked_by=None,
                    summary=None,
                )
            )
            continue
        summaries = task.get("summaries")
        latest = summaries[-1] if summaries else None
        blocked_by = (
            str(latest["blocked_by"])
            if isinstance(latest, dict) and latest.get("blocked_by")
            else None
        )
        outcomes.append(
            _GeneratorOutcome(
                task_id=task_id,
                status=str(task.get("status") or "unknown"),
                blocked_by=blocked_by,
                summary=latest_summary_text(summaries).strip(),
            )
        )
    return outcomes
