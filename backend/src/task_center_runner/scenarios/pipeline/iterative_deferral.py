"""Iterative continuation via partial plan.

Reference scenario for iteration continuation: iteration 1 submits a partial plan
with ``deferred_goal_for_next_iteration``, evaluator passes, iteration coordinator spawns iteration
2 with ``creation_reason=DEFERRED_GOAL_CONTINUATION`` and ``goal=<deferred_goal_for_next_iteration>``.
Iteration 2 submits a full plan, evaluator passes, goal closes succeeded.

Asserts: 2 iterations per goal, iteration 2 has ``creation_reason`` =
``DEFERRED_GOAL_CONTINUATION``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import (
    submit_plan_closes_goal,
    submit_plan_defers_goal,
)

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import (
    preflight_full_plan,
    preflight_defers_plan,
)
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_CONTINUATION_GOAL = (
    "Run a final preflight readback against the continuation iteration "
    "to confirm the harness wired iteration 2 with the partial-plan goal."
)


class IterativeDeferral(ScenarioBase):
    """Iteration 1 partial plan → iteration 2 full plan; both pass."""

    name = "pipeline.iterative_deferral"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_DEFERS_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.iteration.sequence_no == 1:
            return ToolCallSpec(
                submit_plan_defers_goal,
                preflight_defers_plan(deferred_goal_for_next_iteration=_CONTINUATION_GOAL),
            )
        return ToolCallSpec(submit_plan_closes_goal, preflight_full_plan())

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


__all__ = ["IterativeDeferral"]
