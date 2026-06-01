"""Iterative continuation via partial plan.

Reference scenario for iteration continuation: iteration 1 submits a partial plan
with ``deferred_goal_for_next_iteration``, reducer passes, iteration coordinator spawns iteration
2 with ``creation_reason=DEFERRED_GOAL_CONTINUATION`` and ``goal=<deferred_goal_for_next_iteration>``.
Iteration 2 submits a full plan, reducer passes, workflow closes succeeded.

Asserts: 2 iterations per workflow, iteration 2 has ``creation_reason`` =
``DEFERRED_GOAL_CONTINUATION``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios._scenario_helpers import (
    preflight_full_plan,
    preflight_defers_plan,
)
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_CONTINUATION_GOAL = (
    "Run a final preflight readback against the continuation iteration "
    "to confirm the harness wired iteration 2 with the partial-plan goal."
)


class IterativeDeferral(ScenarioBase):
    """Iteration 1 partial plan → iteration 2 full plan; both pass."""

    name = "pipeline.iterative_deferral"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.iteration.sequence_no == 1:
            return ToolCallSpec(
                submit_planner_outcome,
                preflight_defers_plan(deferred_goal_for_next_iteration=_CONTINUATION_GOAL),
            )
        return ToolCallSpec(submit_planner_outcome, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "success", "outcome": "Continuation-iteration preflight evidence accepted."},
        )


__all__ = ["IterativeDeferral"]
