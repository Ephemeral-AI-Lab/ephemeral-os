"""Attempt retry after a generator failure."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _retry_generator_plan() -> dict[str, Any]:
    return {
        "plan_spec": "Run one generator task that fails only on attempt 1.",
        "evaluation_criteria": [
            "Attempt 1 records a terminal generator failure.",
            "Attempt 2 reruns the generator task with a revised task id.",
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


class AttemptRetryGeneratorFailure(ScenarioBase):
    """Attempt 1 generator fails, attempt 2 succeeds."""

    name = "pipeline.attempt_retry_generator_failure"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _retry_generator_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        if ctx.attempt.attempt_sequence_no == 1:
            return ("fail:Intentional first-attempt generator failure.",)
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Generator retry recovered on attempt 2.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["AttemptRetryGeneratorFailure"]
