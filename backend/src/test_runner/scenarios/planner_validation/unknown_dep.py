"""Planner validation - unknown dependency rejected."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unknown_dep_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": []},
            {"id": "b", "agent_name": "executor", "needs": ["z"]},
        ],
        "task_specs": {
            "a": "Run a workspace preflight.",
            "b": "Run after missing dependency z.",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a", "b"],
                "prompt": "Confirm both tasks completed.",
            }
        ],
    }


class PlannerUnknownDep(ScenarioBase):
    """Plan references an unknown local dependency id."""

    name = "planner_validation.unknown_dep"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _unknown_dep_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "failed", "outcome": "Unexpected reducer invocation under unknown dep."},
        )


__all__ = ["PlannerUnknownDep"]
