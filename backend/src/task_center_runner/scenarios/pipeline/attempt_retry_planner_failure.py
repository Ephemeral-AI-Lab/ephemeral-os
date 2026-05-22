"""Attempt retry after an invalid planner submission."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import preflight_full_plan
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unknown_dependency_plan() -> dict[str, Any]:
    return {
        "plan_spec": "Invalid first attempt with an unknown dependency.",
        "evaluation_criteria": ["Planner failure triggers an attempt retry."],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": ["missing"]},
        ],
        "task_specs": {"a": "Run a workspace preflight."},
    }


class AttemptRetryPlannerFailure(ScenarioBase):
    """Attempt 1 planner fails validation, attempt 2 emits a valid plan."""

    name = "pipeline.attempt_retry_planner_failure"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_plan_closes_goal, _unknown_dependency_plan())
        return ToolCallSpec(
            submit_plan_closes_goal,
            preflight_full_plan(
                plan_spec=(
                    "Retry with a valid plan after the planner failure."
                ),
                evaluation_criteria=(
                    "Retry planner saw failed-attempt context.",
                    "Workspace preflight completed.",
                ),
            ),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Planner retry recovered with a valid plan.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["AttemptRetryPlannerFailure"]
