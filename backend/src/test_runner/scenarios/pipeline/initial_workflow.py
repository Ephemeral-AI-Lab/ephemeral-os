"""Initial workflow, single attempt, single success.

Reference scenario for the simplest task_center happy path: TaskCenter entry
creates the initial workflow → planner emits one full plan → executor runs ``preflight`` →
reducer passes → workflow closes succeeded. One workflow, one iteration
(``creation_reason=INITIAL``), one attempt (``attempt_sequence_no=1``).

Use this as the template for any "single-attempt success in a particular
configuration" scenario. Branch on ``ctx.iteration.sequence_no`` and
``ctx.attempt.attempt_sequence_no`` to cover more configurations.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios._scenario_helpers import preflight_full_plan
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


class InitialWorkflow(ScenarioBase):
    """Single workflow, single iteration, single attempt — happy path."""

    name = "pipeline.initial_workflow"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "success", "outcome": "Initial workflow preflight evidence accepted."},
        )


__all__ = ["InitialWorkflow"]
