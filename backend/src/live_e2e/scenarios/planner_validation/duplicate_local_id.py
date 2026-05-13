"""Planner validation — duplicate task ``local_id`` rejected.

Reference scenario for the planner-validation subpackage. The planner emits a
full plan whose ``tasks`` list contains two entries sharing ``id="dup"``.
``ordered_generator_tasks`` (``task_center/attempt/generator_dag.py``) rejects
the duplicate ids inside the planner submission tool; the orchestrator surfaces
this as a planner failure and closes the attempt with
``fail_reason="planner_failed"``.

Both attempts in the episode run the same invalid plan, so episode 1 closes
with ``status=failed`` and the mission closes ``status=failed``. No generator
or evaluator was launched.

Asserts: ``report.task_center_status == "failed"``; the seen event sequence
contains two planner invocations, no accepted ``PLANNER_FULL_PLAN``, and no
``EXECUTOR_INVOKED`` or ``EVALUATOR_INVOKED`` events.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_failure
from tools.submission.planner import submit_full_plan

from live_e2e.audit.events import EventType
from live_e2e.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _duplicate_local_id_plan() -> dict[str, Any]:
    return {
        "task_specification": (
            "Intentionally invalid plan: two tasks share the same local id."
        ),
        "evaluation_criteria": ["Plan must be rejected by the orchestrator."],
        "tasks": [
            {"id": "dup", "agent_name": "executor", "deps": []},
            {"id": "dup", "agent_name": "executor", "deps": []},
        ],
        "task_specs": {"dup": "Run a workspace preflight."},
    }


class PlannerDuplicateLocalId(ScenarioBase):
    """Planner returns a duplicate-id plan; attempt closes planner_failed."""

    name = "planner_validation.duplicate_local_id"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_INVOKED,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_full_plan, _duplicate_local_id_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        # Should never be invoked — both planner submissions are rejected
        # before the attempt reaches the evaluator stage. The implementation
        # exists only so the scenario satisfies the protocol; its presence in
        # ``expected_event_sequence`` is intentionally omitted.
        return ToolCallSpec(
            submit_evaluation_failure,
            {
                "summary": "Unexpected evaluator invocation under invalid plan.",
                "failed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["PlannerDuplicateLocalId"]
