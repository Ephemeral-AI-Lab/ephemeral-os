"""Failed trial landscape blocks for planner context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes._shared import latest_summary_text
from task_center.trial.state import Trial, TrialStatus

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import TaskStoreProtocol


_MISSING_TASK_ROW_STATUS = "missing task row"
_PREMATURE_STATUSES = frozenset({"failed", "blocked", _MISSING_TASK_ROW_STATUS})
_EMPTY_SUMMARY_PLACEHOLDERS = frozenset({"(empty)", "(no summary recorded)"})

FAILED_TRIAL_LANDSCAPE = ContextBlockKind.FAILED_TRIAL_LANDSCAPE


@dataclass(frozen=True, slots=True)
class _GeneratorOutcome:
    task_id: str
    status: str
    blocked_by: str | None
    summary: str | None


def failed_trial_landscape_blocks(
    *,
    current_trial_id: str | None,
    trials: list[Trial],
    task_store: TaskStoreProtocol | None = None,
) -> list[ContextBlock]:
    failed = sorted(
        (
            t
            for t in trials
            if t.status == TrialStatus.FAILED and t.id != current_trial_id
        ),
        key=lambda t: t.trial_sequence_no,
    )
    return [
        ContextBlock(
            kind=ContextBlockKind.FAILED_TRIAL_LANDSCAPE,
            priority=ContextPriority.HIGH,
            text=_render_failed_trial(t, task_store=task_store),
            source_id=t.id,
            source_kind="trial",
            metadata={
                "trial_sequence_no": str(t.trial_sequence_no),
                "group_heading": "# Prior Failed Trials",
                "subheading": f"Trial {t.trial_sequence_no}",
            },
        )
        for t in failed
    ]


def _render_failed_trial(
    trial: Trial, *, task_store: TaskStoreProtocol | None
) -> str:
    outcomes = _generator_outcomes(trial, task_store=task_store)

    if trial.continuation_goal:
        plan_kind = "partial"
    elif (
        trial.task_specification
        or trial.evaluation_criteria
        or trial.generator_task_ids
    ):
        plan_kind = "full"
    else:
        plan_kind = "unsubmitted"

    sections = [
        "### Accepted Plan\n\n"
        f"Plan type: {plan_kind}\n\n"
        f"Specification:\n{trial.task_specification or '(not submitted)'}",
        _render_generator_outcomes(outcomes),
    ]

    has_premature = any(o.status in _PREMATURE_STATUSES for o in outcomes)
    if not has_premature and task_store and trial.evaluator_task_id is not None:
        evaluator_task = task_store.get_task(trial.evaluator_task_id)
        evaluator_summary = (
            "(missing evaluator task row)"
            if evaluator_task is None
            else latest_summary_text(evaluator_task.get("summaries"))
        )
        criteria_block = (
            "\n".join(f"  - {c}" for c in trial.evaluation_criteria) or "  (none)"
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
    trial: Trial, *, task_store: TaskStoreProtocol | None
) -> list[_GeneratorOutcome]:
    if task_store is None or not trial.generator_task_ids:
        return []

    outcomes: list[_GeneratorOutcome] = []
    for task_id in trial.generator_task_ids:
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
