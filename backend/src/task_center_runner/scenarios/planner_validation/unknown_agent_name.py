"""Planner validation - unknown generator agent rejected."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_failure
from tools.submission.planner import submit_full_plan

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _unknown_agent_plan() -> dict[str, Any]:
    return {
        "task_specification": "Invalid plan references an unregistered agent.",
        "evaluation_criteria": ["Unknown generator agent is rejected."],
        "tasks": [
            {"id": "a", "agent_name": "missing_generator_agent", "deps": []},
        ],
        "task_specs": {"a": "Run with an unknown agent."},
    }


class PlannerUnknownAgentName(ScenarioBase):
    """Plan references an unknown generator-capable agent."""

    name = "planner_validation.unknown_agent_name"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_full_plan, _unknown_agent_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_failure,
            {
                "summary": "Unexpected evaluator invocation under unknown agent.",
                "failed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["PlannerUnknownAgentName"]
