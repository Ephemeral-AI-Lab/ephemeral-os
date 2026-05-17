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

Exercises ``ready_pending_generator_ids`` against a non-trivial DAG: the
dispatcher must (i) honour multi-parent fan-in (d waits on both b and c, g on
both e and f) and (ii) launch siblings (b/c, e/f) in parallel as soon as
their shared upstream completes.

All seven nodes run the lightweight ``preflight`` action; evaluator passes.
Asserts: 7 ``EXECUTOR_INVOKED`` events, goal ``status=succeeded``,
graph_summary shows one goal with one iteration and one passed attempt
containing all seven generator tasks.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _mixed_topology_plan() -> dict[str, Any]:
    spec = (
        "Run a lightweight workspace preflight and report the observed "
        "sandbox root."
    )
    return {
        "plan_spec": (
            "Mixed-topology DAG: a → (b,c) → d → (e,f) → g."
        ),
        "evaluation_criteria": [
            "All seven preflight nodes completed.",
            "Multi-parent dependencies were honoured by the dispatcher.",
        ],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": []},
            {"id": "b", "agent_name": "executor", "deps": ["a"]},
            {"id": "c", "agent_name": "executor", "deps": ["a"]},
            {"id": "d", "agent_name": "executor", "deps": ["b", "c"]},
            {"id": "e", "agent_name": "executor", "deps": ["d"]},
            {"id": "f", "agent_name": "executor", "deps": ["d"]},
            {"id": "g", "agent_name": "executor", "deps": ["e", "f"]},
        ],
        "task_specs": {tid: spec for tid in ("a", "b", "c", "d", "e", "f", "g")},
    }


class DependencyDagMixed(ScenarioBase):
    """Mixed serial + parallel DAG; dispatcher honours fan-in semantics."""

    name = "pipeline.dependency_dag_mixed"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        # Sibling executor events interleave non-deterministically. Focused
        # tests assert the seven invoked/success counts and graph topology.
        EventType.EXECUTOR_INVOKED,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _mixed_topology_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Mixed-topology DAG completed; all fan-in nodes ran.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["DependencyDagMixed"]
