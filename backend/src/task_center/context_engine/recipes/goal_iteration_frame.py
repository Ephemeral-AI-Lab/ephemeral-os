"""Goal / iteration framing blocks shared by multiple recipes.

Owns :func:`goal_iteration_blocks` (the goal / current-iteration frame emitted
by planner and evaluator) and :func:`latest_summary_text` (used by generator,
evaluator, and attempt_landscape to read the most recent summary off a task
row). Living in its own module keeps the consuming recipe modules independent
of each other.

The XML structure produced by this module:

* Iteration 1 (no prior iterations): one standalone block tagged
  ``<goal_current_iteration>``.
* Iteration N ≥ 2: a standalone ``<goal>`` block, then one
  ``<iteration iteration_no="K" status="prior">`` group per prior iteration
  (each wrapping ``<accepted_plan>`` + ``<summary>``), then the current
  iteration's group (``<iteration iteration_no="N" status="current">``) which
  contains an ``<iteration_goal>`` child — and may pick up additional siblings
  (e.g. failed attempts) when later blocks share the same
  :attr:`current_iteration_group_id`.
"""

from __future__ import annotations

from typing import Any

from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.iteration.state import Iteration
from task_center.goal.state import Goal


def current_iteration_group_id(iteration: Iteration) -> str:
    """Shared group key for blocks wrapped in ``<iteration status="current">``."""
    return f"iteration_{iteration.sequence_no}_current"


def current_iteration_group_attrs(iteration: Iteration) -> str:
    return f'iteration_no="{iteration.sequence_no}" status="current"'


_ATTEMPT_PLAN_GROUP_PREFIX = "attempt_plan_"


def attempt_plan_blocks(attempt, *, priority: ContextPriority) -> list[ContextBlock]:
    """Emit the ``<attempt_plan>`` group: ``<plan_spec>`` + optional handoff child.

    Shared by generator and evaluator recipes; only ``priority`` differs
    (generator uses HIGH, evaluator uses REQUIRED). Returns an empty list when
    the attempt has no ``plan_spec``; callers gate on truthiness before
    extending their block list.
    """
    if not attempt.plan_spec:
        return []
    group_id = f"{_ATTEMPT_PLAN_GROUP_PREFIX}{attempt.id}"
    blocks: list[ContextBlock] = [
        ContextBlock(
            kind=ContextBlockKind.TASK_SPECIFICATION,
            priority=priority,
            text=attempt.plan_spec,
            source_id=attempt.id,
            source_kind="attempt",
            metadata={
                "group_id": group_id,
                "group_tag": "attempt_plan",
                "child_tag": "plan_spec",
            },
        )
    ]
    if attempt.deferred_goal_for_next_iteration:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.TASK_SPECIFICATION,
                priority=priority,
                text=attempt.deferred_goal_for_next_iteration,
                source_id=attempt.id,
                source_kind="attempt",
                metadata={
                    "group_id": group_id,
                    "group_tag": "attempt_plan",
                    "child_tag": "deferred_goal_for_next_iteration",
                    "has_deferred_goal_for_next_iteration": "true",
                },
            )
        )
    return blocks


def latest_summary_text(summaries: list[Any] | None) -> str:
    """Return the most recent summary string from a task's summaries list.

    Tasks carry a ``summaries`` list of dicts; both generator (dependency
    summaries) and evaluator (completed-task summaries) want the latest entry,
    preferring ``summary`` then ``outcome``, falling back to a placeholder.
    """
    if not summaries:
        return "(no summary recorded)"
    last = summaries[-1]
    if not isinstance(last, dict):
        return str(last)
    return str(last.get("summary") or last.get("outcome") or "(empty)")


def goal_iteration_blocks(
    *,
    goal: Goal,
    current_iteration: Iteration,
    iterations: list[Iteration],
) -> list[ContextBlock]:
    """Return the goal/iteration frame in LLM-facing semantic order."""
    if current_iteration.sequence_no == 1:
        return [_goal_current_iteration_block(current_iteration)]

    blocks: list[ContextBlock] = [_goal_statement_block(goal)]
    blocks.extend(_prior_iteration_blocks(current=current_iteration, iterations=iterations))
    blocks.append(_current_iteration_goal_child(current_iteration))
    return blocks


def _goal_current_iteration_block(iteration: Iteration) -> ContextBlock:
    """Iteration 1: a single standalone ``<goal_current_iteration>`` block."""
    return ContextBlock(
        kind=ContextBlockKind.ITERATION_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=iteration.goal,
        source_id=iteration.id,
        source_kind="iteration",
        metadata={
            "tag": "goal_current_iteration",
            "iteration_no": str(iteration.sequence_no),
        },
    )


def _goal_statement_block(goal: Goal) -> ContextBlock:
    """Iteration N ≥ 2: standalone ``<goal>`` block."""
    return ContextBlock(
        kind=ContextBlockKind.GOAL_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=goal.goal,
        source_id=goal.id,
        source_kind="goal",
        metadata={"tag": "goal"},
    )


def _current_iteration_goal_child(iteration: Iteration) -> ContextBlock:
    """Child block inside ``<iteration status="current">``: ``<iteration_goal>``."""
    return ContextBlock(
        kind=ContextBlockKind.ITERATION_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=iteration.goal,
        source_id=iteration.id,
        source_kind="iteration",
        metadata={
            "group_id": current_iteration_group_id(iteration),
            "group_tag": "iteration",
            "group_attrs": current_iteration_group_attrs(iteration),
            "child_tag": "iteration_goal",
            "iteration_no": str(iteration.sequence_no),
        },
    )


def _prior_iteration_blocks(
    *,
    current: Iteration,
    iterations: list[Iteration],
) -> list[ContextBlock]:
    priors = sorted(
        (s for s in iterations if s.sequence_no < current.sequence_no),
        key=lambda s: s.sequence_no,
    )
    out: list[ContextBlock] = []
    immediate_prior = current.sequence_no - 1
    for prior in priors:
        if prior.plan_spec is None or prior.task_summary is None:
            raise ContextEngineError(
                f"Prior iteration {prior.id!r} (seq={prior.sequence_no}) is "
                "missing plan_spec or task_summary; chain integrity violated."
            )
        priority = (
            ContextPriority.HIGH
            if prior.sequence_no == immediate_prior
            else ContextPriority.MEDIUM
        )
        group_id = f"iteration_{prior.sequence_no}_prior"
        group_attrs = (
            f'iteration_no="{prior.sequence_no}" status="prior"'
        )
        out.append(
            ContextBlock(
                kind=ContextBlockKind.PRIOR_ITERATION_SPECIFICATION,
                priority=priority,
                text=prior.plan_spec,
                source_id=prior.id,
                source_kind="iteration",
                metadata={
                    "group_id": group_id,
                    "group_tag": "iteration",
                    "group_attrs": group_attrs,
                    "child_tag": "accepted_plan",
                },
            )
        )
        out.append(
            ContextBlock(
                kind=ContextBlockKind.PRIOR_ITERATION_SUMMARY,
                priority=priority,
                text=prior.task_summary,
                source_id=prior.id,
                source_kind="iteration",
                metadata={
                    "group_id": group_id,
                    "group_tag": "iteration",
                    "group_attrs": group_attrs,
                    "child_tag": "summary",
                },
            )
        )
    return out
