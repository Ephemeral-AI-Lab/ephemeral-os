"""Attempt retry on reducer failure.

Reference scenario for the attempt-retry path. Iteration 1 / Attempt 1: planner
emits a full plan, executor runs ``preflight``, reducer returns
``submit_reducer_outcome`` — iteration coordinator creates Attempt 2 (budget
permits). Attempt 2: planner emits a full plan, executor runs ``preflight``,
reducer passes — workflow closes succeeded.

Asserts: 1 iteration with 2 attempts; attempt 1 ``fail_reason="task_failed"``,
attempt 2 ``status=PASSED``; workflow ``status=succeeded``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios._scenario_helpers import preflight_full_plan
from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


class AttemptRetryReducerFailure(ScenarioBase):
    """Attempt 1 fails (reducer), attempt 2 passes — same iteration."""

    name = "pipeline.attempt_retry_reducer_failure"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.attempt.attempt_sequence_no == 1:
            return ToolCallSpec(
                submit_reducer_outcome,
                {
                    "status": "failed",
                    "outcome": (
                        "Intentional reducer failure to exercise the "
                        "single-iteration attempt retry path."
                    ),
                },
            )
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": "Retry attempt accepted after retry context delivered.",
            },
        )


__all__ = ["AttemptRetryReducerFailure"]
