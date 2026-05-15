"""Initial goal, single trial, single success.

Reference scenario for the simplest task_center happy path: entry executor
delegates → planner emits one full plan → executor runs ``preflight`` →
evaluator passes → goal closes succeeded. One goal, one iteration
(``creation_reason=INITIAL``), one trial (``trial_sequence_no=1``).

Use this as the template for any "single-trial success in a particular
configuration" scenario. Branch on ``ctx.iteration.sequence_no`` and
``ctx.trial.trial_sequence_no`` to cover more configurations.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_full_plan

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import preflight_full_plan
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


class InitialGoal(ScenarioBase):
    """Single goal, single iteration, single trial — happy path."""

    name = "pipeline.initial_mission"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_full_plan, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Initial goal preflight evidence accepted.",
                "passed_criteria": list(ctx.trial.evaluation_criteria),
            },
        )


__all__ = ["InitialGoal"]
