"""Trial retry after a generator failure."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_full_plan

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _retry_generator_plan() -> dict[str, Any]:
    return {
        "task_specification": "Run one generator task that fails only on trial 1.",
        "evaluation_criteria": [
            "Trial 1 records a terminal generator failure.",
            "Trial 2 reruns the generator task with a revised task id.",
        ],
        "tasks": [
            {"id": "generator_retry_probe", "agent_name": "executor", "deps": []},
        ],
        "task_specs": {
            "generator_retry_probe": (
                "ACTION fail_once_then_preflight reason=generator_retry"
            ),
        },
    }


class TrialRetryGeneratorFailure(ScenarioBase):
    """Trial 1 generator fails, trial 2 succeeds."""

    name = "pipeline.attempt_retry_generator_failure"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_FAILURE,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_full_plan, _retry_generator_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        if ctx.trial.trial_sequence_no == 1:
            return ("fail:Intentional first-trial generator failure.",)
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Generator retry recovered on trial 2.",
                "passed_criteria": list(ctx.trial.evaluation_criteria),
            },
        )


__all__ = ["TrialRetryGeneratorFailure"]
