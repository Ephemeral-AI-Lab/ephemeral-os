"""Stage strategies — one dispatch hook per :class:`AttemptStage`.

The previous dispatch loop was a hand-rolled ``if attempt.stage == PLAN /
elif GENERATE / elif EVALUATE`` chain inside :class:`AttemptDispatcher`.
Adding a stage (e.g. ``REVIEW`` between ``EVALUATE`` and ``CLOSED``)
required editing four files. This module replaces the chain with a stage
strategy table — adding a stage is one new strategy class plus one entry
in :data:`STAGE_STRATEGIES`.

The strategies are intentionally tiny: each implements
:meth:`dispatch_ready_work` only. Submission routing
(``apply_plan_submission``, ``apply_generator_submission``,
``apply_evaluator_submission``) stays on :class:`AttemptOrchestrator`
because those entry points are stage-typed at the public API boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from task_center.attempt.state import AttemptStage

if TYPE_CHECKING:
    from task_center.attempt.dispatcher import AttemptDispatcher
    from task_center.attempt.state import Attempt


class StageStrategy(Protocol):
    """Hook called once per ``dispatch_ready_work`` invocation."""

    def dispatch(
        self, dispatcher: AttemptDispatcher, attempt: Attempt
    ) -> None: ...


class _PlanStage:
    """No-op: planning advances only on planner submission, not ready work."""

    def dispatch(self, dispatcher: AttemptDispatcher, attempt: Attempt) -> None:
        del dispatcher, attempt


class _GenerateStage:
    """Drive the generator DAG: ready, quiescence, fail-or-evaluator handoff."""

    def dispatch(self, dispatcher: AttemptDispatcher, attempt: Attempt) -> None:
        dispatcher._dispatch_generating(attempt)


class _EvaluateStage:
    """Read evaluator status; close attempt on terminal."""

    def dispatch(self, dispatcher: AttemptDispatcher, attempt: Attempt) -> None:
        dispatcher._dispatch_evaluating(attempt)


class _ClosedStage:
    """Terminal — no further dispatch."""

    def dispatch(self, dispatcher: AttemptDispatcher, attempt: Attempt) -> None:
        del dispatcher, attempt


STAGE_STRATEGIES: dict[AttemptStage, StageStrategy] = {
    AttemptStage.PLAN: _PlanStage(),
    AttemptStage.GENERATE: _GenerateStage(),
    AttemptStage.EVALUATE: _EvaluateStage(),
    AttemptStage.CLOSED: _ClosedStage(),
}


__all__ = ["STAGE_STRATEGIES", "StageStrategy"]
