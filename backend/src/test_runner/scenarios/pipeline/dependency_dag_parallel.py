"""Dependency DAG - three roots fan into one final task."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _parallel_fanin_plan() -> dict[str, Any]:
    task_spec = "Run a lightweight workspace preflight and report the observed sandbox root."
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": []},
            {"id": "b", "agent_name": "executor", "needs": []},
            {"id": "c", "agent_name": "executor", "needs": []},
            {"id": "d", "agent_name": "executor", "needs": ["a", "b", "c"]},
        ],
        "task_specs": {task_id: task_spec for task_id in ("a", "b", "c", "d")},
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a", "b", "c", "d"],
                "prompt": "Confirm roots a/b/c completed before fan-in task d.",
            }
        ],
    }


class DependencyDagParallel(ScenarioBase):
    """Focused fan-in scenario: a, b, c -> d."""

    name = "pipeline.dependency_dag_parallel"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _parallel_fanin_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "success", "outcome": "Parallel fan-in DAG completed."},
        )


__all__ = ["DependencyDagParallel"]
