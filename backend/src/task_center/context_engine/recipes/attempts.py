"""``<attempt>`` emitters for the planner and evaluator recipes.

Two emitters with different shapes:

* :func:`failed_attempt_blocks` — **planner-only**. One block per failed prior
  attempt, grouped under ``<iteration position="current">`` via
  :func:`current_iteration_group_id`, attrs ``attempt_no="k"`` only (the
  attempt is a prior attempt *of the current iteration*, so a ``status`` /
  ``verdict`` would be misleading). Each block's ``text`` is the pre-rendered
  XML body of ``<attempt>…</attempt>``: one ``<task id status>`` per
  **terminal** generator (un-started excluded), an ``<evaluator_summary>``
  *only when the evaluator ran*, and a ``<failure>`` line. No ``<plan_spec>`` /
  ``<evaluation_criteria>`` / ``<status_summary>`` /
  ``<deferred_goal_for_next_iteration>``.
* :func:`current_attempt_flat_blocks` — **evaluator-only**. The attempt being
  judged, emitted as flat top-level blocks (no ``<iteration>`` / ``<attempt>``
  wrapper): ``<plan_spec>`` (framing) + one ``<task id status>`` per generator
  task (summary-only) + ``<evaluation_criteria>`` (authority). No
  ``<deferred_goal_for_next_iteration>`` — the evaluator judges the current
  slice against its criteria, not the deferred remainder.

The pre-rendered failed-attempt body bypasses the renderer's structural-closer
guard, so :func:`sanitize_fragment` re-applies it on each embedded fragment.
The flat current-attempt blocks are ordinary blocks; the renderer guards their
bodies directly (a handoff outcome opts out via ``pre_rendered_xml``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.attempt.state import Attempt, AttemptStatus
from task_center._core.generator_summaries import (
    attempt_failure_line,
    generator_outcomes,
    latest_task_summary,
)
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes._task_xml import (
    block_task_body,
    render_task_element,
    sanitize_fragment,
    task_attrs,
)
from task_center.context_engine.recipes.iterations import (
    current_iteration_group_attrs,
    current_iteration_group_id,
)
from task_center.iteration.state import Iteration

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import TaskStoreProtocol


# ``ContextBlock.kind`` for the flat evaluator emitter's task and criteria
# blocks (the plan_spec block reuses ``TASK_SPECIFICATION``). ``kind`` is a
# free string; tag resolution goes through ``metadata['tag']``, so these are
# provenance labels only.
_TASK_OUTCOME_KIND = "generator_task_outcome"
_EVALUATION_CRITERIA_KIND = "evaluation_criteria"


def failed_attempt_blocks(
    *,
    current_attempt_id: str | None,
    iteration: Iteration,
    attempts: list[Attempt],
    task_store: TaskStoreProtocol | None = None,
) -> list[ContextBlock]:
    """Return one ``<attempt attempt_no="k">`` block per failed prior attempt."""
    failed = sorted(
        (t for t in attempts if t.status == AttemptStatus.FAILED and t.id != current_attempt_id),
        key=lambda t: t.attempt_sequence_no,
    )
    group_id = current_iteration_group_id(iteration)
    group_attrs = current_iteration_group_attrs(iteration)
    return [
        ContextBlock(
            kind=ContextBlockKind.FAILED_ATTEMPT,
            priority=ContextPriority.HIGH,
            text=_render_failed_attempt_body(t, task_store=task_store),
            source_id=t.id,
            source_kind="attempt",
            metadata={
                "group_id": group_id,
                "group_tag": "iteration",
                "group_attrs": group_attrs,
                "child_tag": "attempt",
                "attrs": f'attempt_no="{t.attempt_sequence_no}"',
                "pre_rendered_xml": "true",
            },
        )
        for t in failed
    ]


def current_attempt_flat_blocks(
    *,
    attempt: Attempt,
    task_store: TaskStoreProtocol | None = None,
) -> list[ContextBlock]:
    """Return the current attempt's substance as flat top-level blocks (evaluator-only).

    Emitted in order, with no ``<iteration>`` / ``<attempt>`` wrapper:

    * ``<plan_spec>`` (HIGH) — the attempt's framing, built fresh from
      ``attempt.plan_spec``. No ``<deferred_goal_for_next_iteration>`` child:
      the evaluator judges the current slice, not the deferred remainder.
    * one ``<task id="..." status="...">`` per generator outcome (HIGH),
      body = the latest summary text (empty body when none; a handoff outcome
      nests its child ``<task>``s and opts out of the structural guard).
    * ``<evaluation_criteria>`` (REQUIRED — the authority, last dropped under
      token budget), omitted when the attempt carries no criteria.

    Empty list when the planner has not submitted a plan yet (no plan_spec).
    """
    if not attempt.plan_spec:
        return []
    blocks: list[ContextBlock] = [
        ContextBlock(
            kind=ContextBlockKind.TASK_SPECIFICATION,
            priority=ContextPriority.HIGH,
            text=attempt.plan_spec,
            source_id=attempt.id,
            source_kind="attempt",
            metadata={"tag": "plan_spec"},
        )
    ]
    for task_id, outcome in zip(
        attempt.generator_task_ids,
        generator_outcomes(attempt, task_store=task_store),
        strict=True,
    ):
        blocks.append(_task_outcome_block(task_id, outcome))
    if attempt.evaluation_criteria:
        blocks.append(
            ContextBlock(
                kind=_EVALUATION_CRITERIA_KIND,
                priority=ContextPriority.REQUIRED,
                text="\n".join(attempt.evaluation_criteria),
                source_id=attempt.id,
                source_kind="attempt",
                metadata={"tag": "evaluation_criteria"},
            )
        )
    return blocks


def _task_outcome_block(task_id: str, outcome) -> ContextBlock:
    """One flat ``<task id status>`` block, body = the generator summary."""
    text, pre_rendered = block_task_body(outcome)
    metadata = {"tag": "task", "attrs": task_attrs(outcome)}
    if pre_rendered:
        metadata["pre_rendered_xml"] = "true"
    return ContextBlock(
        kind=_TASK_OUTCOME_KIND,
        priority=ContextPriority.HIGH,
        text=text,
        source_id=task_id,
        source_kind="task_center_task",
        metadata=metadata,
    )


def _render_failed_attempt_body(attempt: Attempt, *, task_store: TaskStoreProtocol | None) -> str:
    """Render the inside of ``<attempt attempt_no="k">…</attempt>``.

    Terminal generator ``<task>``s (un-started excluded), then an
    ``<evaluator_summary>`` only when the evaluator actually ran, then a
    ``<failure>`` line.
    """
    parts = [
        render_task_element(outcome, source_id=attempt.id)
        for outcome in generator_outcomes(attempt, task_store=task_store)
        if outcome.is_terminal
    ]
    evaluator_summary = _evaluator_summary_if_ran(attempt, task_store=task_store)
    if evaluator_summary is not None:
        parts.append(
            "<evaluator_summary>\n"
            + sanitize_fragment(evaluator_summary, attempt.id)
            + "\n</evaluator_summary>"
        )
    failure = sanitize_fragment(attempt_failure_line(attempt, task_store), attempt.id)
    parts.append(f"<failure>\n{failure}\n</failure>")
    return "\n".join(parts)


def _evaluator_summary_if_ran(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> str | None:
    """The evaluator's latest summary text, or ``None`` if it never ran."""
    if task_store is None or attempt.evaluator_task_id is None:
        return None
    task = task_store.get_task(attempt.evaluator_task_id)
    if task is None or not task.get("summaries"):
        return None
    return latest_task_summary(task.get("summaries"))


__all__ = [
    "current_attempt_flat_blocks",
    "failed_attempt_blocks",
]
