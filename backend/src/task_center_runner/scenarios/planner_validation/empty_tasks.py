"""Planner validation - empty full plan rejected.

The planner submits a plan with no generator tasks and no reducers. The
submission schema requires at least one ``task``, one ``task_spec``, and one
``reducer`` (``SharedPlannerSubmissionInput``), so the tool rejects the empty
plan at its boundary before any orchestrator dispatch. The attempt closes with
``fail_reason="task_failed"`` and no generator or reducer task is created.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.reducer import submit_reduction_failure
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _empty_tasks_plan() -> dict[str, Any]:
    return {
        "tasks": [],
        "task_specs": {},
        "reducers": [],
    }


class PlannerEmptyTasks(ScenarioBase):
    """Full plan with no generator tasks and no reducers."""

    name = "planner_validation.empty_tasks"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _empty_tasks_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reduction_failure,
            {"outcome": "Unexpected reducer invocation under empty plan."},
        )


__all__ = ["PlannerEmptyTasks"]
