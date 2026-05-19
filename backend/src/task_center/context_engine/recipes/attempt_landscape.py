"""``<attempt>`` blocks nested under ``<iteration status="current">``.

Two emitters, one body shape:

* :func:`failed_attempt_landscape_blocks` — one block per failed prior attempt,
  attrs ``status="prior" verdict="fail"`` (planner + evaluator).
* :func:`current_attempt_block` — one block for the attempt currently being
  evaluated, attrs ``status="current"`` (evaluator only).

Every block's ``text`` is the pre-rendered XML body of ``<attempt>…</attempt>``;
the renderer groups them under ``<iteration status="current">`` via the shared
:func:`current_iteration_group_id`.

The body inlines children as siblings — no ``<attempt_plan>``,
``<generator_outcomes>``, or ``<evaluator_judgment>`` wrappers — matching the
``OPTIMIZED_USER_MSG_1.md`` spec:

* ``<plan_spec>`` and (when present) ``<deferred_goal_for_next_iteration>``.
* ``<status_summary>`` (failed only — current attempts haven't finished yet
  so they emit per-task ``<task>`` children but no status_summary).
* One ``<task id="..." status="...">`` per generator task, body = the latest
  summary text.
* ``<evaluation_criteria>`` — listed at attempt scope.
* Evaluator-only on failed attempts: ``<evaluator_summary>``,
  ``<passed_criteria>``, ``<failed_criteria>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.attempt.state import Attempt, AttemptFailReason, AttemptStatus
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
from task_center.iteration.state import Iteration

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import TaskStoreProtocol


_MISSING_TASK_ROW_STATUS = "missing task row"
_PREMATURE_STATUSES = frozenset({"failed", "blocked", _MISSING_TASK_ROW_STATUS})
_EMPTY_SUMMARY_PLACEHOLDERS = frozenset({"(empty)", "(no summary recorded)"})

# fail_reasons where no plan was ever committed, so neither generators nor
# the evaluator had a chance to run. The recipe collapses such attempts to a
# minimal body with explicit "bypassed" status attributes.
_NO_DOWNSTREAM_STAGES = frozenset(
    {AttemptFailReason.PLANNER_FAILED, AttemptFailReason.STARTUP_FAILED}
)

# Closers a recipe MUST refuse to leak into user content. The body is
# hand-assembled XML, so the renderer's structural guard is bypassed; this
# guard takes its place.
_STRUCTURAL_CLOSERS: tuple[str, ...] = (
    "</plan_spec>",
    "</deferred_goal_for_next_iteration>",
    "</status_summary>",
    "</task>",
    "</evaluation_criteria>",
    "</evaluator_summary>",
    "</passed_criteria>",
    "</failed_criteria>",
    "</attempt>",
    "</iteration>",
)


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
    """Return one ``<attempt status="prior" verdict="fail">`` block per failed prior."""
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
                "attrs": (
                    f'attempt_no="{t.attempt_sequence_no}" '
                    'status="prior" verdict="fail"'
                ),
                "pre_rendered_xml": "true",
            },
        )
        for t in failed
    ]


def current_attempt_block(
    *,
    attempt: Attempt,
    iteration: Iteration,
    task_store: TaskStoreProtocol | None = None,
) -> list[ContextBlock]:
    """Return the active ``<attempt status="current">`` block, or empty list.

    Empty list when the planner hasn't submitted a plan yet (no plan_spec —
    nothing to show).
    """
    if not attempt.plan_spec:
        return []
    body = _render_current_attempt_body(attempt, task_store=task_store)
    group_id = current_iteration_group_id(iteration)
    group_attrs = current_iteration_group_attrs(iteration)
    return [
        ContextBlock(
            kind=ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE,
            priority=ContextPriority.REQUIRED,
            text=body,
            source_id=attempt.id,
            source_kind="attempt",
            metadata={
                "group_id": group_id,
                "group_tag": "iteration",
                "group_attrs": group_attrs,
                "child_tag": "attempt",
                "attrs": (
                    f'attempt_no="{attempt.attempt_sequence_no}" '
                    'status="current"'
                ),
                "pre_rendered_xml": "true",
                # Surface deferred-goal presence for any downstream code that
                # wants to branch on it (e.g. role skills, audits).
                **(
                    {"has_deferred_goal_for_next_iteration": "true"}
                    if attempt.deferred_goal_for_next_iteration
                    else {}
                ),
            },
        )
    ]


def _render_failed_attempt_body(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    """Render the inside of ``<attempt status="prior" verdict="fail">…</attempt>``."""
    if attempt.fail_reason in _NO_DOWNSTREAM_STAGES:
        reason = attempt.fail_reason.value
        return (
            f'<plan_spec status="unsubmitted"/>\n'
            f'<status_summary status="not_started"/>\n'
            f'<evaluator_summary status="bypassed" reason="{reason}"/>'
        )
    parts: list[str] = []
    parts.append(_render_plan_spec_children(attempt))
    parts.append(_render_outcome_children(attempt, task_store=task_store))
    parts.append(
        _render_evaluator_children(attempt, task_store=task_store)
    )
    return "\n".join(p for p in parts if p)


def _render_current_attempt_body(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    """Render the inside of ``<attempt status="current">…</attempt>``.

    Current attempts haven't finished evaluation yet, so the body omits the
    evaluator's verdict children (``<evaluator_summary>``,
    ``<passed_criteria>``, ``<failed_criteria>``). Generator ``<task>``
    children carry whatever summaries the runtime has recorded so far.
    """
    parts: list[str] = []
    parts.append(_render_plan_spec_children(attempt))
    parts.append(_render_task_children(attempt, task_store=task_store))
    parts.append(_render_evaluation_criteria(attempt))
    return "\n".join(p for p in parts if p)


def _render_plan_spec_children(attempt: Attempt) -> str:
    """Emit ``<plan_spec>`` and optional ``<deferred_goal_for_next_iteration>``."""
    plan_spec = _sanitize_user_text(attempt.plan_spec or "(not submitted)", attempt.id)
    pieces = [f"<plan_spec>\n{plan_spec}\n</plan_spec>"]
    if attempt.deferred_goal_for_next_iteration:
        handoff = _sanitize_user_text(
            attempt.deferred_goal_for_next_iteration, attempt.id
        )
        pieces.append(
            f"<deferred_goal_for_next_iteration>\n{handoff}\n"
            "</deferred_goal_for_next_iteration>"
        )
    return "\n".join(pieces)


def _render_outcome_children(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    """Emit ``<status_summary>`` and one ``<task>`` per generator task."""
    outcomes = _generator_outcomes(attempt, task_store=task_store)
    if not outcomes:
        return "<status_summary>(no generator tasks recorded)</status_summary>"
    status_summary = "\n".join(
        (
            f"{o.task_id}: {o.status} by {o.blocked_by}"
            if o.blocked_by
            else f"{o.task_id}: {o.status}"
        )
        for o in outcomes
    )
    parts: list[str] = ["<status_summary>", status_summary, "</status_summary>"]
    parts.extend(_render_task_element(o, attempt.id) for o in outcomes)
    return "\n".join(parts)


def _render_task_children(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str:
    """Emit one ``<task>`` per generator task (no status_summary wrapper)."""
    outcomes = _generator_outcomes(attempt, task_store=task_store)
    if not outcomes:
        return ""
    return "\n".join(_render_task_element(o, attempt.id) for o in outcomes)


def _render_task_element(outcome: _GeneratorOutcome, attempt_id: str) -> str:
    if outcome.summary and outcome.summary not in _EMPTY_SUMMARY_PLACEHOLDERS:
        body = _sanitize_user_text(outcome.summary, attempt_id)
        return (
            f'<task id="{outcome.task_id}" status="{outcome.status}">\n'
            f"{body}\n</task>"
        )
    return f'<task id="{outcome.task_id}" status="{outcome.status}"/>'


def _render_evaluation_criteria(attempt: Attempt) -> str:
    if not attempt.evaluation_criteria:
        return ""
    body = "\n".join(
        _sanitize_user_text(c, attempt.id) for c in attempt.evaluation_criteria
    )
    return f"<evaluation_criteria>\n{body}\n</evaluation_criteria>"


def _render_evaluator_children(
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
            '<evaluator_summary status="bypassed" reason="generator_failed">\n'
            f"{reason}\n"
            "</evaluator_summary>"
        )
    parts: list[str] = [_render_evaluation_criteria(attempt)]
    if task_store is None or attempt.evaluator_task_id is None:
        parts.append(
            "<evaluator_summary>\n(no evaluator summary recorded)\n"
            "</evaluator_summary>"
        )
        return "\n".join(p for p in parts if p)
    evaluator_task = task_store.get_task(attempt.evaluator_task_id)
    evaluator_summary = (
        "(missing evaluator task row)"
        if evaluator_task is None
        else latest_summary_text(evaluator_task.get("summaries"))
    )
    parts.append(
        "<evaluator_summary>\n"
        + _sanitize_user_text(evaluator_summary, attempt.id)
        + "\n</evaluator_summary>"
    )
    passed, failed = _evaluator_verdicts(evaluator_task)
    if passed:
        parts.append(
            "<passed_criteria>\n"
            + "\n".join(_sanitize_user_text(c, attempt.id) for c in passed)
            + "\n</passed_criteria>"
        )
    if failed:
        parts.append(
            "<failed_criteria>\n"
            + "\n".join(_sanitize_user_text(c, attempt.id) for c in failed)
            + "\n</failed_criteria>"
        )
    return "\n".join(p for p in parts if p)


def _sanitize_user_text(text: str, source_id: str) -> str:
    """Raise if user-supplied text contains a structural closer this body emits."""
    for closer in _STRUCTURAL_CLOSERS:
        if closer in text:
            raise ContextEngineError(
                f"Attempt body for {source_id!r} contains structural "
                f"closer {closer!r}. Rewrite the offending field to avoid this "
                "closer, or surface it under a different ContextBlockKind."
            )
    return text


def _evaluator_verdicts(
    evaluator_task: dict | None,
) -> tuple[list[str], list[str]]:
    """Pull passed_criteria / failed_criteria from the evaluator task's latest payload."""
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
