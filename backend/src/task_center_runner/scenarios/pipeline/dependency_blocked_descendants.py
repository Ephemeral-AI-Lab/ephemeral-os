"""Blocked ancestor leaves downstream generator descendants not started."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_failure
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unreachable_pending_plan() -> dict[str, Any]:
    return {
        "plan_spec": (
            "Block root task a and prove descendants b, c, and d never launch."
        ),
        "evaluation_criteria": [
            "Downstream descendants of blocked task a remained pending.",
            "No evaluator launched for the failed generator stage.",
        ],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": []},
            {"id": "b", "agent_name": "executor", "deps": ["a"]},
            {"id": "c", "agent_name": "executor", "deps": ["a"]},
            {"id": "d", "agent_name": "executor", "deps": ["b", "c"]},
        ],
        "task_specs": {
            "a": "ACTION fail_root reason=unreachable_pending",
            "b": "This task must remain pending behind a.",
            "c": "This task must remain pending behind a.",
            "d": "This fan-in task must remain pending behind b and c.",
        },
    }


class DependencyBlockedDescendants(ScenarioBase):
    """Blocked root leaves descendants pending until the attempt fails."""

    name = "pipeline.dependency_blocked_descendants"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_FAILURE,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_FAILURE,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _unreachable_pending_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        if "ACTION fail_root" in (ctx.context_message or ""):
            return ("fail:Intentional root failure for blocked-descendant coverage.",)
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_failure,
            {
                "summary": "Unexpected evaluator invocation after unreachable pending descendants.",
                "failed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["DependencyBlockedDescendants"]
