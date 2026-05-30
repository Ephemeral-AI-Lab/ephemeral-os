"""Recursive workflow success and failure scenarios."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_plan_closes_goal
from tools.submission.reducer import (
    submit_reduction_failure,
    submit_reduction_success,
)

from task_center_runner.scenarios._scenario_helpers import is_recursive_workflow
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _entry_origin_nested_plan(*, failing_child: bool) -> dict[str, Any]:
    package_id = "child_failure" if failing_child else "child_success"
    return {
        "tasks": [
            {"id": "delegate_child", "agent_name": "executor", "needs": []},
            {
                "id": "recursive_return_guard",
                "agent_name": "executor",
                "needs": ["delegate_child"],
            },
            {
                "id": "parent_reconciliation",
                "agent_name": "executor",
                "needs": ["recursive_return_guard"],
            },
        ],
        "task_specs": {
            "delegate_child": f"ACTION request_recursive_workflow package={package_id}",
            "recursive_return_guard": "VERIFY checkpoint=recursive_return",
            "parent_reconciliation": (
                "Run parent reconciliation after recursive close report."
            ),
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": [
                    "delegate_child",
                    "recursive_return_guard",
                    "parent_reconciliation",
                ],
                "prompt": (
                    "Confirm the child workflow closed before parent reconciliation."
                ),
            }
        ],
    }


def _child_success_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "child_a", "agent_name": "executor", "needs": []},
            {"id": "child_b", "agent_name": "executor", "needs": ["child_a"]},
        ],
        "task_specs": {
            "child_a": "ACTION recursive_execute slice=a",
            "child_b": "ACTION recursive_reconcile slice=b",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["child_a", "child_b"],
                "prompt": "Confirm both child slices completed.",
            }
        ],
    }


def _child_failure_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "child_always_fails", "agent_name": "executor", "needs": []},
        ],
        "task_specs": {
            "child_always_fails": "ACTION child_failure reason=nested_workflow",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["child_always_fails"],
                "prompt": "Confirm the child task completed (never reached).",
            }
        ],
    }


class NestedWorkflow(ScenarioBase):
    """Parent generator delegates to a child workflow, then reconciles."""

    name = "pipeline.nested_workflow"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if is_recursive_workflow(ctx):
            return ToolCallSpec(submit_plan_closes_goal, _child_success_plan())
        return ToolCallSpec(
            submit_plan_closes_goal,
            _entry_origin_nested_plan(failing_child=False),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ""
        if "request_recursive_workflow" in context_message:
            return ("request_recursive_workflow:child_success",)
        if "ACTION recursive_" in context_message:
            return ("recursive_step",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reduction_success,
            {"outcome": "Nested workflow completed before parent reconciliation."},
        )

    def recursive_handoff_goal(self, ctx: ScenarioContext) -> str | None:  # noqa: ARG002
        return "Run the delegated child workflow and return a close report."


class NestedWorkflowFailure(ScenarioBase):
    """Child workflow exhausts attempts and parent workflow fails cleanly."""

    name = "pipeline.nested_workflow_failure"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if is_recursive_workflow(ctx):
            return ToolCallSpec(submit_plan_closes_goal, _child_failure_plan())
        return ToolCallSpec(
            submit_plan_closes_goal,
            _entry_origin_nested_plan(failing_child=True),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ""
        if "request_recursive_workflow" in context_message:
            return ("request_recursive_workflow:child_failure",)
        if "child_failure" in context_message:
            return ("fail:Intentional child workflow failure.",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        # Never reached: the child workflow fails, so the delegating generator
        # fails the parent attempt before the reducer's needs are satisfied.
        return ToolCallSpec(
            submit_reduction_failure,
            {"outcome": "Nested workflow failure should not reach the reducer."},
        )

    def recursive_handoff_goal(self, ctx: ScenarioContext) -> str | None:  # noqa: ARG002
        return "Run a child workflow that intentionally exhausts attempts."


__all__ = ["NestedWorkflow", "NestedWorkflowFailure"]
