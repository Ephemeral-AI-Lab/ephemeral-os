"""Planner validation - unknown generator agent rejected."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unknown_agent_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "a", "agent_name": "missing_generator_agent", "needs": []},
        ],
        "task_specs": {"a": "Run with an unknown agent."},
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a"],
                "prompt": "Confirm the task completed.",
            }
        ],
    }


class PlannerUnknownAgentName(ScenarioBase):
    """Plan references an unknown generator-capable agent."""

    name = "planner_validation.unknown_agent_name"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _unknown_agent_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "failed", "outcome": "Unexpected reducer invocation under unknown agent."},
        )


__all__ = ["PlannerUnknownAgentName"]
