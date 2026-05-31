"""Blocked ancestor leaves downstream generator descendants not started."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unreachable_pending_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": []},
            {"id": "b", "agent_name": "executor", "needs": ["a"]},
            {"id": "c", "agent_name": "executor", "needs": ["a"]},
            {"id": "d", "agent_name": "executor", "needs": ["b", "c"]},
        ],
        "task_specs": {
            "a": "ACTION fail_root reason=unreachable_pending",
            "b": "This task must remain pending behind a.",
            "c": "This task must remain pending behind a.",
            "d": "This fan-in task must remain pending behind b and c.",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a", "b", "c", "d"],
                "prompt": "Confirm blocked root a left descendants pending.",
            }
        ],
    }


class DependencyBlockedDescendants(ScenarioBase):
    """Blocked root leaves descendants pending until the attempt fails."""

    name = "pipeline.dependency_blocked_descendants"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _unreachable_pending_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        if "ACTION fail_root" in (ctx.context_message or ""):
            return ("fail:Intentional root failure for blocked-descendant coverage.",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        # Never reached: the blocked root fails the attempt before the
        # reducer's needs are satisfied. Stub satisfies the protocol.
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "failed",
                "outcome": "Unexpected reducer invocation after unreachable pending descendants.",
            },
        )


__all__ = ["DependencyBlockedDescendants"]
