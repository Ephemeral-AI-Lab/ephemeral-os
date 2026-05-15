"""Planner validation - partial plan requires a continuation goal."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_failure
from tools.submission.planner import submit_partial_plan

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _partial_without_goal() -> dict[str, Any]:
    return {
        "task_specification": "Invalid partial plan with no continuation goal.",
        "evaluation_criteria": ["Partial plan must declare a continuation goal."],
        "tasks": [{"id": "a", "agent_name": "executor", "deps": []}],
        "task_specs": {"a": "Run a workspace preflight."},
    }


class PlannerPartialWithoutContinuationGoal(ScenarioBase):
    """submit_partial_plan call omits required continuation_goal."""

    name = "planner_validation.partial_without_continuation_goal"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_partial_plan, _partial_without_goal())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_failure,
            {
                "summary": "Unexpected evaluator invocation under invalid partial.",
                "failed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["PlannerPartialWithoutContinuationGoal"]
