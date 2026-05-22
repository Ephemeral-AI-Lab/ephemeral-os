"""Dependency DAG - three roots fan into one final task."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _parallel_fanin_plan() -> dict[str, Any]:
    task_spec = (
        "Run a lightweight workspace preflight and report the observed "
        "sandbox root."
    )
    return {
        "plan_spec": "Run parallel roots a, b, c before fan-in task d.",
        "evaluation_criteria": [
            "Root tasks a, b, and c completed.",
            "Fan-in task d launched only after all three parents completed.",
        ],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": []},
            {"id": "b", "agent_name": "executor", "deps": []},
            {"id": "c", "agent_name": "executor", "deps": []},
            {"id": "d", "agent_name": "executor", "deps": ["a", "b", "c"]},
        ],
        "task_specs": {task_id: task_spec for task_id in ("a", "b", "c", "d")},
    }


class DependencyDagParallel(ScenarioBase):
    """Focused fan-in scenario: a, b, c -> d."""

    name = "pipeline.dependency_dag_parallel"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _parallel_fanin_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Parallel fan-in DAG completed.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["DependencyDagParallel"]
