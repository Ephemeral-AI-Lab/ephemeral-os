"""Planner validation - empty full plan rejected."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_failure
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _empty_tasks_plan() -> dict[str, Any]:
    return {
        "task_specification": "Invalid full plan with no generator tasks.",
        "evaluation_criteria": ["Empty full plans are rejected before dispatch."],
        "tasks": [],
        "task_specs": {},
    }


class PlannerEmptyTasks(ScenarioBase):
    """Full plan with no generator tasks."""

    name = "planner_validation.empty_tasks"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _empty_tasks_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_failure,
            {
                "summary": "Unexpected evaluator invocation under empty plan.",
                "failed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["PlannerEmptyTasks"]
