"""Planner validation - dependency cycle rejected."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_failure
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _cycle_plan() -> dict[str, Any]:
    return {
        "plan_spec": "Invalid plan: a depends on b and b depends on a.",
        "evaluation_criteria": ["Dependency cycles are rejected."],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": ["b"]},
            {"id": "b", "agent_name": "executor", "deps": ["a"]},
        ],
        "task_specs": {
            "a": "Run after b.",
            "b": "Run after a.",
        },
    }


class PlannerCycleInDeps(ScenarioBase):
    """Plan contains a dependency cycle."""

    name = "planner_validation.cycle_in_deps"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _cycle_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_failure,
            {
                "summary": "Unexpected evaluator invocation under cyclic plan.",
                "failed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["PlannerCycleInDeps"]
