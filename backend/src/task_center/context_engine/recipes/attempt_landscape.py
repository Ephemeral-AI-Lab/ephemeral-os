"""Failed attempt landscape blocks for planner context.

Each failed attempt produces one block whose ``text`` is the pre-rendered XML
body of ``<attempt attempt_no="N" status="failed">…</attempt>``. The renderer
groups every failed-attempt block (plus the current iteration's
``<iteration_goal>`` child when present) under the same
``<iteration status="current">`` parent via the shared
:func:`current_iteration_group_id`.

The body contains:

* ``<attempt_plan>`` with nested ``<plan_spec>`` and (when present)
  ``<deferred_goal_for_next_iteration>`` children — both wrap planner-supplied text.
* ``<generator_outcomes>`` with a recipe-generated ``<status_summary>`` and one
  ``<task id="..." status="...">`` child per generator task. Per-task summary
  text is user-supplied and so gets the hostile-body sanitizer applied below.
* ``<evaluator_judgment status="bypassed" reason="generator_failed">`` when the
  attempt died before the evaluator ran; ``<evaluator_judgment status="ran"
  verdict="fail">`` otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes.goal_iteration_frame import (
    current_iteration_group_attrs,
    current_iteration_group_id,
    latest_summary_text,
)
from task_center.attempt.state import Attempt, AttemptFailReason, AttemptStatus
from task_center.iteration.state import Iteration

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import TaskStoreProtocol


_MISSING_TASK_ROW_STATUS = "missing task row"
_PREMATURE_STATUSES = frozenset({"failed", "blocked", _MISSING_TASK_ROW_STATUS})
_EMPTY_SUMMARY_PLACEHOLDERS = frozenset({"(empty)", "(no summary recorded)"})

# fail_reasons where no plan was ever committed, so neither generators nor
# the evaluator had a chance to run. The recipe collapses such attempts to a
# minimal body with explicit "bypassed" status attributes — emitting an
# `<evaluator_judgment status="ran" verdict="fail">` block with
# "(no evaluator summary recorded)" would lie about whether the evaluator ran.
_NO_DOWNSTREAM_STAGES = frozenset(
    {AttemptFailReason.PLANNER_FAILED, AttemptFailReason.STARTUP_FAILED}
)

# Closers a recipe MUST refuse to leak into user content. Kept here (not in the
# renderer) because the recipe is the layer that embeds user text into a
# structured XML body; the renderer only sees the final assembled text.
_STRUCTURAL_CLOSERS: tuple[str, ...] = (
    "</attempt_plan>",
    "</plan_spec>",
    "</deferred_goal_for_next_iteration>",
    "</generator_outcomes>",
    "</status_summary>",
    "</task>",
    "</evaluator_judgment>",
    "</attempt>",
    "</iteration>",
)

FAILED_ATTEMPT_LANDSCAPE = ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE


@dataclass(frozen=True, slots=True)
class _GeneratorOutcome:
    task_id: str
    status: str
    blocked_by: str | None
    summary: str | None


def failed_attempt_landscape_blocks(
    *,
    current_attempt_id: str | None,
    iteration: Iteration,
    attempts: list[Attempt],
    task_store: TaskStoreProtocol | None = None,
) -> list[ContextBlock]:
    failed = sorted(
        (
            t
            for t in attempts
            if t.status == AttemptStatus.FAILED and t.id != current_attempt_id
        ),
        key=lambda t: t.attempt_sequence_no,
    )
    group_id = current_iteration_group_id(iteration)
    group_attrs = current_iteration_group_attrs(iteration)
    return [
        ContextBlock(
            kind=ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE,
            priority=ContextPriority.HIGH,
            text=_render_failed_attempt_body(t, task_store=task_store),
            source_id=t.id,
            source_kind="attempt",
            metadata={
                "group_id": group_id,
                "group_tag": "iteration",
                "group_attrs": group_attrs,
                "child_tag": "attempt",
                "attrs": f'attempt_no="{t.attempt_sequence_no}" status="failed"',
                # The body is hand-assembled XML; the recipe sanitizes the
                # user-supplied fragments it embeds.
                "pre_rendered_xml": "true",
            },
        )
        for t in failed
    ]


def _render_failed_attempt_body(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    """Render the inside of ``<attempt attempt_no="N" status="failed">…</attempt>``."""
    if attempt.fail_reason in _NO_DOWNSTREAM_STAGES:
        reason = attempt.fail_reason.value
        return (
            f'<attempt_plan status="unsubmitted"/>\n'
            f'<generator_outcomes status="not_started"/>\n'
            f'<evaluator_judgment status="bypassed" reason="{reason}"/>'
        )
    sections: list[str] = [
        _render_attempt_plan(attempt),
        _render_generator_outcomes_xml(attempt, task_store=task_store),
        _render_evaluator_judgment(attempt, task_store=task_store),
    ]
    return "\n".join(sections)


def _render_attempt_plan(attempt: Attempt) -> str:
    plan_spec = _sanitize_user_text(attempt.plan_spec or "(not submitted)", attempt.id)
    children = [f"<plan_spec>\n{plan_spec}\n</plan_spec>"]
    if attempt.deferred_goal_for_next_iteration:
        handoff = _sanitize_user_text(attempt.deferred_goal_for_next_iteration, attempt.id)
        children.append(
            f"<deferred_goal_for_next_iteration>\n{handoff}\n</deferred_goal_for_next_iteration>"
        )
    return "<attempt_plan>\n" + "\n".join(children) + "\n</attempt_plan>"


def _render_generator_outcomes_xml(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    outcomes = _generator_outcomes(attempt, task_store=task_store)
    if not outcomes:
        status_summary = "(no generator tasks recorded)"
    else:
        status_summary = "\n".join(
            (
                f"{o.task_id}: {o.status} by {o.blocked_by}"
                if o.blocked_by
                else f"{o.task_id}: {o.status}"
            )
            for o in outcomes
        )
    parts: list[str] = [
        "<generator_outcomes>",
        "<status_summary>",
        status_summary,
        "</status_summary>",
    ]
    for o in outcomes:
        if o.summary and o.summary not in _EMPTY_SUMMARY_PLACEHOLDERS:
            body = _sanitize_user_text(o.summary, attempt.id)
            parts.append(
                f'<task id="{o.task_id}" status="{o.status}">\n{body}\n</task>'
            )
        else:
            parts.append(
                f'<task id="{o.task_id}" status="{o.status}"/>'
            )
    parts.append("</generator_outcomes>")
    return "\n".join(parts)


def _render_evaluator_judgment(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    outcomes = _generator_outcomes(attempt, task_store=task_store)
    has_premature = any(o.status in _PREMATURE_STATUSES for o in outcomes)
    if has_premature:
        failed_ids = sorted(
            o.task_id for o in outcomes if o.status in _PREMATURE_STATUSES
        )
        reason = (
            "Evaluator skipped because generator task(s) failed: "
            f"{', '.join(failed_ids)}."
            if failed_ids
            else "Evaluator skipped: generator outcomes never recorded."
        )
        return (
            '<evaluator_judgment status="bypassed" reason="generator_failed">\n'
            f"{reason}\n"
            "</evaluator_judgment>"
        )
    if task_store is None or attempt.evaluator_task_id is None:
        return (
            '<evaluator_judgment status="ran" verdict="fail">\n'
            "(no evaluator summary recorded)\n"
            "</evaluator_judgment>"
        )
    evaluator_task = task_store.get_task(attempt.evaluator_task_id)
    evaluator_summary = (
        "(missing evaluator task row)"
        if evaluator_task is None
        else latest_summary_text(evaluator_task.get("summaries"))
    )
    body_parts: list[str] = []
    criteria_lines = "\n".join(
        _sanitize_user_text(c, attempt.id) for c in attempt.evaluation_criteria
    ) or "(none)"
    body_parts.append(
        "<evaluation_criteria>\n" + criteria_lines + "\n</evaluation_criteria>"
    )
    body_parts.append(
        "<evaluator_summary>\n"
        + _sanitize_user_text(evaluator_summary, attempt.id)
        + "\n</evaluator_summary>"
    )
    passed, failed = _evaluator_verdicts(evaluator_task)
    if passed:
        body_parts.append(
            "<passed_criteria>\n"
            + "\n".join(_sanitize_user_text(c, attempt.id) for c in passed)
            + "\n</passed_criteria>"
        )
    if failed:
        body_parts.append(
            "<failed_criteria>\n"
            + "\n".join(_sanitize_user_text(c, attempt.id) for c in failed)
            + "\n</failed_criteria>"
        )
    return (
        '<evaluator_judgment status="ran" verdict="fail">\n'
        + "\n".join(body_parts)
        + "\n</evaluator_judgment>"
    )


def _sanitize_user_text(text: str, source_id: str) -> str:
    """Raise if user-supplied text contains a structural closer this body emits."""
    for closer in _STRUCTURAL_CLOSERS:
        if closer in text:
            raise ContextEngineError(
                f"Failed-attempt body for {source_id!r} contains structural "
                f"closer {closer!r}. Rewrite the offending field to avoid this "
                "closer, or surface it under a different ContextBlockKind."
            )
    return text


def _evaluator_verdicts(
    evaluator_task: dict | None,
) -> tuple[list[str], list[str]]:
    """Pull passed_criteria / failed_criteria from the evaluator task's latest payload.

    The orchestrator persists the evaluator submission as
    ``summaries[-1] = {"outcome", "summary", "payload": {...}}``; payload may
    carry ``passed_criteria`` (success path) or ``failed_criteria`` (failure
    path). Missing keys or non-list values collapse to empty lists so the
    caller can branch with ``if passed:`` / ``if failed:`` without further
    defensive checks.
    """
    if evaluator_task is None:
        return [], []
    summaries = evaluator_task.get("summaries")
    if not summaries:
        return [], []
    latest = summaries[-1]
    if not isinstance(latest, dict):
        return [], []
    payload = latest.get("payload") or {}
    if not isinstance(payload, dict):
        return [], []

    def _str_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item]

    return _str_list(payload.get("passed_criteria")), _str_list(
        payload.get("failed_criteria")
    )


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
                summary=latest_summary_text(summaries),
            )
        )
    return outcomes
