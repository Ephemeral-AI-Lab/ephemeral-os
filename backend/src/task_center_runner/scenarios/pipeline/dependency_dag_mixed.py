"""Dependency DAG — mixed serial + parallel topology.

Plan shape::

           a
          / \\
         b   c       (parallel after a)
          \\ /
           d         (serial — waits for both b AND c)
          / \\
         e   f       (parallel after d)
          \\ /
           g         (final — waits for both e AND f)

Exercises ``ready_pending_plan_ids`` against a non-trivial DAG: the
task dispatcher must (i) honour multi-parent fan-in (d waits on both b and c, g on
both e and f) and (ii) launch siblings (b/c, e/f) in parallel as soon as
their shared upstream completes.

All seven nodes run the lightweight ``preflight`` action; reducer passes.
Asserts: 7 ``EXECUTOR_INVOKED`` events, workflow ``status=succeeded``,
graph_summary shows one workflow with one iteration and one passed attempt
containing all seven generator tasks.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_plan_closes_goal
from tools.submission.reducer import submit_reduction_success

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _mixed_topology_plan() -> dict[str, Any]:
    spec = (
        "Run a lightweight workspace preflight and report the observed "
        "sandbox root."
    )
    task_ids = ("a", "b", "c", "d", "e", "f", "g")
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": []},
            {"id": "b", "agent_name": "executor", "needs": ["a"]},
            {"id": "c", "agent_name": "executor", "needs": ["a"]},
            {"id": "d", "agent_name": "executor", "needs": ["b", "c"]},
            {"id": "e", "agent_name": "executor", "needs": ["d"]},
            {"id": "f", "agent_name": "executor", "needs": ["d"]},
            {"id": "g", "agent_name": "executor", "needs": ["e", "f"]},
        ],
        "task_specs": {tid: spec for tid in task_ids},
        "reducers": [
            {
                "id": "reduce",
                "needs": list(task_ids),
                "prompt": "Confirm all seven nodes ran and multi-parent fan-in held.",
            }
        ],
    }


class DependencyDagMixed(ScenarioBase):
    """Mixed serial + parallel DAG; task dispatcher honours fan-in semantics."""

    name = "pipeline.dependency_dag_mixed"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _mixed_topology_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reduction_success,
            {"outcome": "Mixed-topology DAG completed; all fan-in nodes ran."},
        )


__all__ = ["DependencyDagMixed"]
