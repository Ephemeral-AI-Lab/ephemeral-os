"""Planner validation - partial plan requires a continuation goal.

The planner calls ``submit_plan_defers_goal`` with an otherwise well-formed plan
but omits the required ``deferred_goal_for_next_iteration``. The defers schema
requires that field (nonblank), so the tool rejects the submission and the
attempt closes with ``fail_reason="task_failed"`` without deferring.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.reducer import submit_reduction_failure
from tools.submission.planner import submit_plan_defers_goal

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _defers_without_goal() -> dict[str, Any]:
    return {
        "tasks": [{"id": "a", "agent_name": "executor", "needs": []}],
        "task_specs": {"a": "Run a workspace preflight."},
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a"],
                "prompt": "Confirm the task completed.",
            }
        ],
    }


class PlannerDefersWithoutDeferredGoal(ScenarioBase):
    """submit_plan_defers_goal call omits required deferred_goal_for_next_iteration."""

    name = "planner_validation.defers_without_deferred_goal"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_defers_goal, _defers_without_goal())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reduction_failure,
            {"outcome": "Unexpected reducer invocation under invalid partial."},
        )


__all__ = ["PlannerDefersWithoutDeferredGoal"]
