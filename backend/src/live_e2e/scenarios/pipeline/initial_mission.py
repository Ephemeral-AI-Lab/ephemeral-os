"""Initial mission, single attempt, single success.

Reference scenario for the simplest task_center happy path: entry executor
delegates → planner emits one full plan → executor runs ``preflight`` →
evaluator passes → mission closes succeeded. One mission, one episode
(``creation_reason=INITIAL``), one attempt (``attempt_sequence_no=1``).

Use this as the template for any "single-attempt success in a particular
configuration" scenario. Branch on ``ctx.episode.sequence_no`` and
``ctx.attempt.attempt_sequence_no`` to cover more configurations.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.main_agent.evaluator import submit_evaluation_success
from tools.submission.main_agent.planner import submit_full_plan

from live_e2e.audit.events import EventType
from live_e2e.scenarios._utils import preflight_full_plan
from live_e2e.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


class InitialMission(ScenarioBase):
    """Single mission, single episode, single attempt — happy path."""

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
                "summary": "Initial mission preflight evidence accepted.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["InitialMission"]
