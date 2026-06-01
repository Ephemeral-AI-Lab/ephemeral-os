"""Attempt retry after a generator failure."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _retry_generator_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "generator_retry_probe", "agent_name": "executor", "needs": []},
        ],
        "task_specs": {
            "generator_retry_probe": ("ACTION fail_once_then_preflight reason=generator_retry"),
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["generator_retry_probe"],
                "prompt": "Confirm the generator task recovered on the retry attempt.",
            }
        ],
    }


class AttemptRetryGeneratorFailure(ScenarioBase):
    """Attempt 1 generator fails, attempt 2 succeeds."""

    name = "pipeline.attempt_retry_generator_failure"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _retry_generator_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        if ctx.attempt.attempt_sequence_no == 1:
            return ("fail:Intentional first-attempt generator failure.",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "success", "outcome": "Generator retry recovered on attempt 2."},
        )


__all__ = ["AttemptRetryGeneratorFailure"]
