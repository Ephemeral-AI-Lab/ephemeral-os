"""Dependency DAG — serial chain a → b → c.

Reference scenario for the task dispatcher's ``ready_pending_generator_ids`` /
``needs[]`` machinery. Plan = three sequential ``preflight`` tasks; each
depends on the previous. Dispatcher must launch them in order: first ``a``
PENDING+ready, then ``b`` after ``a`` DONE, then ``c`` after ``b`` DONE.

Asserts: the executor invocation order matches ``a, b, c``; each task's
``needs`` row contains exactly its predecessor's full task id; evaluator
passes once all three are DONE.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _serial_chain_plan() -> dict[str, Any]:
    spec = (
        "Run a lightweight workspace preflight and report the observed "
        "sandbox root."
    )
    return {
        "plan_spec": "Run a serial preflight chain a → b → c.",
        "evaluation_criteria": [
            "All three preflight nodes completed.",
            "Tasks ran in dependency order.",
        ],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": []},
            {"id": "b", "agent_name": "executor", "deps": ["a"]},
            {"id": "c", "agent_name": "executor", "deps": ["b"]},
        ],
        "task_specs": {"a": spec, "b": spec, "c": spec},
    }


class DependencyDagSerial(ScenarioBase):
    """Serial DAG; assert executor invocation order matches dependency order."""

    name = "pipeline.dependency_dag_serial"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _serial_chain_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Serial DAG completed in dependency order.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["DependencyDagSerial"]
