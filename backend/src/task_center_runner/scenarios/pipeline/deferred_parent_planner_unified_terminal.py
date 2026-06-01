"""Partial parent executor keeps one planner terminal across parent and child.

The entry-origin workflow's first iteration submits a partial plan with
``deferred_goal_for_next_iteration``. Its executor then requests a child
workflow. Both parent and child planners use ``submit_planner_outcome``; nested
deferral policy is enforced by prehooks while the profile terminal set stays
unified.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

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


class DeferredParentPlannerUnifiedTerminal(ScenarioBase):
    """Child workflow from a partial parent keeps the unified planner terminal."""

    name = "pipeline.deferred_parent_planner_unified_terminal"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if is_recursive_workflow(ctx):
            return ToolCallSpec(submit_planner_outcome, _child_full_plan())
        if ctx.iteration.sequence_no == 1:
            return ToolCallSpec(submit_planner_outcome, _entry_origin_defers_plan())
        return ToolCallSpec(
            submit_planner_outcome,
            preflight_full_plan(
                criteria=(
                    "The entry-origin continuation iteration completed as a full plan.",
                ),
            ),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        instruction = ctx.instruction or ""
        if "request_recursive_workflow" in instruction:
            return (f"request_recursive_workflow:{_CHILD_PACKAGE_ID}",)
        if "ACTION recursive_" in instruction:
            return ("recursive_step",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "success", "outcome": "Unified planner terminal scenario branch passed."},
        )

    def recursive_handoff_goal(self, ctx: ScenarioContext) -> str | None:  # noqa: ARG002
        return _CHILD_GOAL


__all__ = ["DeferredParentPlannerUnifiedTerminal"]
