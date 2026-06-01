"""Attempt retry after an invalid planner submission."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios._scenario_helpers import preflight_full_plan
from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unknown_dependency_plan() -> dict[str, Any]:
    # Task ``a`` needs an unknown id, so the gate rejects this plan with
    # "unknown needs" — driving the attempt-1 planner failure / retry path.
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": ["missing"]},
        ],
        "task_specs": {"a": "Run a workspace preflight."},
        "reducers": [
            {"id": "reduce", "needs": ["a"], "prompt": "Confirm task a completed."},
        ],
    }


class AttemptRetryPlannerFailure(ScenarioBase):
    """Attempt 1 planner fails validation, attempt 2 emits a valid plan."""

    name = "pipeline.attempt_retry_planner_failure"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_planner_outcome, _unknown_dependency_plan())
        return ToolCallSpec(
            submit_planner_outcome,
            preflight_full_plan(
                criteria=(
                    "Retry planner saw failed-attempt context.",
                    "Workspace preflight completed.",
                ),
            ),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "success", "outcome": "Planner retry recovered with a valid plan."},
        )


__all__ = ["AttemptRetryPlannerFailure"]
