"""Full-system capacity matrix scenario.

This scenario is the capacity-suite facade over the existing full-stack
adversarial flow. It keeps the proven TaskCenter/sandbox/LSP choreography and
adds a capacity-specific metrics artifact before the final release guard.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from task_center_runner.scenarios.base import ScenarioContext
from task_center_runner.scenarios.full_stack_adversarial import FullStackAdversarial


class FullSystemCapacityMatrix(FullStackAdversarial):
    """Composite capacity run across TaskCenter, sandbox, plugins, and audit."""

    name = "capacity.full_system_capacity_matrix"

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ctx.prompt or ""
        if "ACTION capacity_metrics_full_system" in context_message:
            return ("capacity_metrics_full_system",)
        return super().executor_actions(ctx)

    def _final_plan(self, ctx: ScenarioContext) -> dict[str, Any]:
        plan = super()._final_plan(ctx)
        tasks = list(plan["tasks"])
        task_specs = dict(plan["task_specs"])

        tasks.append(
            {
                "id": "capacity_metrics_summary",
                "agent_name": "executor",
                "needs": ["final_reconciliation_check"],
            }
        )
        for task in tasks:
            if task["id"] == "final_release_guard":
                task["needs"] = ["capacity_metrics_summary"]
                break

        task_specs["capacity_metrics_summary"] = (
            "ACTION capacity_metrics_full_system profile=project"
        )
        plan["tasks"] = tasks
        plan["task_specs"] = task_specs
        plan["reducers"] = [
            {
                "id": "reduce",
                "needs": [task["id"] for task in tasks],
                "prompt": (
                    "Final reconciliation passed and the capacity metrics "
                    "artifact uses the task_center_runner.capacity.v1 schema."
                ),
            }
        ]
        return plan


__all__ = ["FullSystemCapacityMatrix"]
