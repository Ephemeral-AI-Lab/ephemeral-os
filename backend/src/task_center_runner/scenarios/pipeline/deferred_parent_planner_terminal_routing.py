"""Partial parent executor routes a child planner to a restricted terminal set.

The entry-origin workflow's first iteration submits a partial plan with
``deferred_goal_for_next_iteration``. Its executor then requests a child workflow. Because the
child workflow's parent task belongs to that partial-planned attempt, the child
planner must launch as ``planner`` without a defer terminal. The entry-origin
continuation iteration still launches ``planner`` with both planner terminals.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import (
    submit_plan_closes_goal,
    submit_plan_defers_goal,
)
from tools.submission.reducer import submit_reduction_success

from task_center_runner.scenarios._scenario_helpers import (
    is_recursive_workflow,
    minimal_full_plan,
    preflight_full_plan,
)
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_CHILD_PACKAGE_ID = "partial_parent_child"
_CHILD_GOAL = (
    "Resolve the delegated child workflow requested by an executor whose parent "
    "attempt submitted a partial plan."
)
_CONTINUATION_GOAL = (
    "Run the entry-origin follow-up iteration after the delegated child workflow has "
    "returned its close report."
)


def _entry_origin_defers_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "delegate_child", "agent_name": "executor", "needs": []},
            {
                "id": "recursive_return_guard",
                "agent_name": "executor",
                "needs": ["delegate_child"],
            },
        ],
        "task_specs": {
            "delegate_child": (
                f"ACTION request_recursive_workflow package={_CHILD_PACKAGE_ID}"
            ),
            "recursive_return_guard": "VERIFY checkpoint=recursive_return",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["delegate_child", "recursive_return_guard"],
                "prompt": (
                    "Confirm the child workflow close report reached the parent."
                ),
            }
        ],
        "deferred_goal_for_next_iteration": _CONTINUATION_GOAL,
    }


def _child_full_plan() -> dict[str, Any]:
    return minimal_full_plan(
        criteria=["The child workflow completes through a full plan."],
        task_id="child_reconcile",
        task_spec=(
            "ACTION recursive_reconcile slice=full_only_planner. Write the "
            "standard recursive close report for the parent."
        ),
    )


class DeferredParentPlannerTerminalRouting(ScenarioBase):
    """Child workflow from a partial parent gets the restricted planner terminals."""

    name = "pipeline.deferred_parent_planner_terminal_routing"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if is_recursive_workflow(ctx):
            return ToolCallSpec(submit_plan_closes_goal, _child_full_plan())
        if ctx.iteration.sequence_no == 1:
            return ToolCallSpec(submit_plan_defers_goal, _entry_origin_defers_plan())
        return ToolCallSpec(
            submit_plan_closes_goal,
            preflight_full_plan(
                criteria=(
                    "The entry-origin continuation iteration completed as a full plan.",
                ),
            ),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ""
        if "request_recursive_workflow" in context_message:
            return (f"request_recursive_workflow:{_CHILD_PACKAGE_ID}",)
        if "ACTION recursive_" in context_message:
            return ("recursive_step",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reduction_success,
            {"outcome": "Planner routing scenario branch passed."},
        )

    def recursive_handoff_goal(self, ctx: ScenarioContext) -> str | None:  # noqa: ARG002
        return _CHILD_GOAL


__all__ = ["DeferredParentPlannerTerminalRouting"]
