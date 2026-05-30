"""Dependency DAG - diamond topology."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _diamond_plan() -> dict[str, Any]:
    task_spec = (
        "Run a lightweight workspace preflight and report the observed "
        "sandbox root."
    )
    return {
        "plan_spec": "Run diamond graph a -> b,c -> d.",
        "evaluation_criteria": [
            "Task a completed before b and c.",
            "Task d received dependency results from b and c.",
        ],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": []},
            {"id": "b", "agent_name": "executor", "deps": ["a"]},
            {"id": "c", "agent_name": "executor", "deps": ["a"]},
            {"id": "d", "agent_name": "executor", "deps": ["b", "c"]},
        ],
        "task_specs": {task_id: task_spec for task_id in ("a", "b", "c", "d")},
    }


class DependencyDagDiamond(ScenarioBase):
    """Diamond readiness and dependency-summary rendering scenario."""

    name = "pipeline.dependency_dag_diamond"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _diamond_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Diamond DAG completed.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["DependencyDagDiamond"]
