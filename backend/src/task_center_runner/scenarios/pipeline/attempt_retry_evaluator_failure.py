"""Attempt retry on evaluator failure.

Reference scenario for the attempt-retry path. Iteration 1 / Attempt 1: planner
emits a full plan, executor runs ``preflight``, evaluator returns
``submit_evaluation_failure`` — iteration-manager creates Attempt 2 (budget
permits). Attempt 2: planner emits a full plan, executor runs ``preflight``,
evaluator passes — goal closes succeeded.

Asserts: 1 iteration with 2 attempts; attempt 1 ``fail_reason="evaluator_failed"``,
attempt 2 ``status=PASSED``; goal ``status=succeeded``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import preflight_full_plan
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


class AttemptRetryEvaluatorFailure(ScenarioBase):
    """Attempt 1 fails (evaluator), attempt 2 passes — same iteration."""

    name = "pipeline.attempt_retry_evaluator_failure"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_FAILURE,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.attempt.attempt_sequence_no == 1:
            return ToolCallSpec(
                submit_evaluation_failure,
                {
                    "summary": (
                        "Intentional evaluator failure to exercise the "
                        "single-iteration attempt retry path."
                    ),
                    "failed_criteria": ["Workspace preflight completed."],
                },
            )
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Retry attempt accepted after retry context delivered.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["AttemptRetryEvaluatorFailure"]
