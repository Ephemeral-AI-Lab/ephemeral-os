"""Iterative continuation via partial plan.

Reference scenario for iteration continuation: iteration 1 submits a partial plan
with ``continuation_goal``, evaluator passes, iteration-manager spawns iteration
2 with ``creation_reason=PARTIAL_CONTINUATION`` and ``goal=<continuation_goal>``.
Iteration 2 submits a full plan, evaluator passes, goal closes succeeded.

Asserts: 2 iterations per goal, iteration 2 has ``creation_reason`` =
``PARTIAL_CONTINUATION``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import (
    submit_full_plan,
    submit_partial_plan,
)

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import (
    preflight_full_plan,
    preflight_partial_plan,
)
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_CONTINUATION_GOAL = (
    "Run a final preflight readback against the continuation iteration "
    "to confirm the harness wired iteration 2 with the partial-plan goal."
)


class IterativeContinuation(ScenarioBase):
    """Iteration 1 partial plan → iteration 2 full plan; both pass."""

    name = "pipeline.iterative_continuation"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_PARTIAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.iteration.sequence_no == 1:
            return ToolCallSpec(
                submit_partial_plan,
                preflight_partial_plan(continuation_goal=_CONTINUATION_GOAL),
            )
        return ToolCallSpec(submit_full_plan, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Continuation-iteration preflight evidence accepted.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["IterativeContinuation"]
