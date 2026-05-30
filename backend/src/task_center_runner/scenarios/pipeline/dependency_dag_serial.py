"""Dependency DAG — serial chain a → b → c.

Reference scenario for the task dispatcher's ``ready_pending_plan_ids`` /
``needs[]`` machinery. Plan = three sequential ``preflight`` tasks; each
depends on the previous. Dispatcher must launch them in order: first ``a``
PENDING+ready, then ``b`` after ``a`` DONE, then ``c`` after ``b`` DONE.

Asserts: the executor invocation order matches ``a, b, c``; each task's
``needs`` row contains exactly its predecessor's full task id; reducer
passes once all three are DONE.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_plan_closes_goal
from tools.submission.reducer import submit_reduction_success

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _serial_chain_plan() -> dict[str, Any]:
    spec = (
        "Run a lightweight workspace preflight and report the observed "
        "sandbox root."
    )
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": []},
            {"id": "b", "agent_name": "executor", "needs": ["a"]},
            {"id": "c", "agent_name": "executor", "needs": ["b"]},
        ],
        "task_specs": {"a": spec, "b": spec, "c": spec},
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a", "b", "c"],
                "prompt": "Confirm all three preflight nodes ran in dependency order.",
            }
        ],
    }


class DependencyDagSerial(ScenarioBase):
    """Serial DAG; assert executor invocation order matches dependency order."""

    name = "pipeline.dependency_dag_serial"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _serial_chain_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reduction_success,
            {"outcome": "Serial DAG completed in dependency order."},
        )


__all__ = ["DependencyDagSerial"]
