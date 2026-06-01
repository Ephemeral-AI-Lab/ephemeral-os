"""Dependency DAG - diamond topology."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _diamond_plan() -> dict[str, Any]:
    task_spec = "Run a lightweight workspace preflight and report the observed sandbox root."
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": []},
            {"id": "b", "agent_name": "executor", "needs": ["a"]},
            {"id": "c", "agent_name": "executor", "needs": ["a"]},
            {"id": "d", "agent_name": "executor", "needs": ["b", "c"]},
        ],
        "task_specs": {task_id: task_spec for task_id in ("a", "b", "c", "d")},
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a", "b", "c", "d"],
                "prompt": "Confirm task a ran before b/c and d received b/c results.",
            }
        ],
    }


class DependencyDagDiamond(ScenarioBase):
    """Diamond readiness and dependency-summary rendering scenario."""

    name = "pipeline.dependency_dag_diamond"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _diamond_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "success", "outcome": "Diamond DAG completed."},
        )


__all__ = ["DependencyDagDiamond"]
