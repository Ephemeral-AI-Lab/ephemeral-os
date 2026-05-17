"""Goal / iteration framing blocks shared by multiple recipes.

Owns :func:`goal_iteration_blocks` (the goal / current-iteration frame
emitted by planner and evaluator) and :func:`latest_summary_text` (used
by generator, evaluator, and attempt_landscape to read the most recent
summary off a task row). Living in its own module keeps the consuming
recipe modules independent of each other.
"""

from __future__ import annotations

from typing import Any

from task_center.context_engine.core import ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.iteration.state import Iteration
from task_center.goal.state import Goal

GOAL_ITERATION_HEADING = "# Goal / Current Iteration"
GOAL_HEADING = "# Goal"
CURRENT_ITERATION_HEADING = "# Current Iteration"


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
        return [_iteration_statement_block(current_iteration, heading=GOAL_ITERATION_HEADING)]

    return [
        _goal_statement_block(goal),
        *_prior_iteration_blocks(
            current=current_iteration,
            iterations=iterations,
        ),
        _iteration_statement_block(current_iteration, heading=CURRENT_ITERATION_HEADING),
    ]


def _iteration_statement_block(iteration: Iteration, *, heading: str) -> ContextBlock:
    return ContextBlock(
        kind=ContextBlockKind.ITERATION_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=iteration.goal,
        source_id=iteration.id,
        source_kind="iteration",
        metadata={"heading": heading},
    )


def _goal_statement_block(goal: Goal) -> ContextBlock:
    return ContextBlock(
        kind=ContextBlockKind.GOAL_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=goal.goal,
        source_id=goal.id,
        source_kind="goal",
        metadata={
            "group_heading": GOAL_HEADING,
            "subheading": "Goal",
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
        if prior.task_specification is None or prior.task_summary is None:
            raise ContextEngineError(
                f"Prior iteration {prior.id!r} (seq={prior.sequence_no}) is "
                "missing task_specification or task_summary; chain integrity violated."
            )
        priority = (
            ContextPriority.HIGH
            if prior.sequence_no == immediate_prior
            else ContextPriority.MEDIUM
        )
        base_meta = {
            "iteration_sequence_no": str(prior.sequence_no),
            "group_heading": GOAL_HEADING,
        }
        out.append(
            ContextBlock(
                kind=ContextBlockKind.PRIOR_ITERATION_SPECIFICATION,
                priority=priority,
                text=prior.task_specification,
                source_id=prior.id,
                source_kind="iteration",
                metadata={
                    **base_meta,
                    "subheading": f"Iteration {prior.sequence_no} accepted plan",
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
                    **base_meta,
                    "subheading": f"Iteration {prior.sequence_no} summary",
                },
            )
        )
    return out
