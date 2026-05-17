"""Planner validation - unknown dependency rejected."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_failure
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unknown_dep_plan() -> dict[str, Any]:
    return {
        "plan_spec": "Invalid plan: task b depends on unknown local id z.",
        "evaluation_criteria": ["Unknown dependency is rejected."],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": []},
            {"id": "b", "agent_name": "executor", "deps": ["z"]},
        ],
        "task_specs": {
            "a": "Run a workspace preflight.",
            "b": "Run after missing dependency z.",
        },
    }


class PlannerUnknownDep(ScenarioBase):
    """Plan references an unknown local dependency id."""

    name = "planner_validation.unknown_dep"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _unknown_dep_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_failure,
            {
                "summary": "Unexpected evaluator invocation under unknown dep.",
                "failed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["PlannerUnknownDep"]
