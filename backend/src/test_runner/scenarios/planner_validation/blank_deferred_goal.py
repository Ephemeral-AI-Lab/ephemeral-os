"""Planner validation - deferred goal must be nonblank when supplied.

The planner calls ``submit_planner_outcome`` with an otherwise well-formed plan
but supplies a blank ``deferred_goal_for_next_iteration``. Omitted/null means
the plan leaves no remaining current-iteration items, but a supplied string
must name the concrete deferred items, so the tool rejects the submission.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _blank_deferred_goal_plan() -> dict[str, Any]:
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
        "deferred_goal_for_next_iteration": " ",
    }


class PlannerBlankDeferredGoal(ScenarioBase):
    """submit_planner_outcome call supplies blank deferred_goal_for_next_iteration."""

    name = "planner_validation.blank_deferred_goal"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _blank_deferred_goal_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "failed", "outcome": "Unexpected reducer invocation under invalid partial."},
        )


__all__ = ["PlannerBlankDeferredGoal"]
