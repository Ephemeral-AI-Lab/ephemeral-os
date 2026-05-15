"""Trial retry after an invalid planner submission."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_full_plan

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import preflight_full_plan
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unknown_dependency_plan() -> dict[str, Any]:
    return {
        "task_specification": "Invalid first trial with an unknown dependency.",
        "evaluation_criteria": ["Planner failure triggers an trial retry."],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": ["missing"]},
        ],
        "task_specs": {"a": "Run a workspace preflight."},
    }


class TrialRetryPlannerFailure(ScenarioBase):
    """Trial 1 planner fails validation, trial 2 emits a valid plan."""

    name = "pipeline.attempt_retry_planner_failure"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.trial.trial_sequence_no == 1:
            return ToolCallSpec(submit_full_plan, _unknown_dependency_plan())
        return ToolCallSpec(
            submit_full_plan,
            preflight_full_plan(
                task_specification=(
                    "Retry with a valid plan after the planner failure."
                ),
                evaluation_criteria=(
                    "Retry planner saw failed-trial context.",
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
                "passed_criteria": list(ctx.trial.evaluation_criteria),
            },
        )


__all__ = ["TrialRetryPlannerFailure"]
