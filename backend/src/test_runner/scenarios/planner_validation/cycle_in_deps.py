"""Planner validation - dependency cycle rejected."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _cycle_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": ["b"]},
            {"id": "b", "agent_name": "executor", "needs": ["a"]},
        ],
        "task_specs": {
            "a": "Run after b.",
            "b": "Run after a.",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a", "b"],
                "prompt": "Confirm both tasks completed.",
            }
        ],
    }


class PlannerCycleInDeps(ScenarioBase):
    """Plan contains a dependency cycle."""

    name = "planner_validation.cycle_in_deps"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _cycle_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "failed", "outcome": "Unexpected reducer invocation under cyclic plan."},
        )


__all__ = ["PlannerCycleInDeps"]
