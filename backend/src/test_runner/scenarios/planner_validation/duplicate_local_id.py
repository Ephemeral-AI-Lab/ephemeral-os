"""Planner validation — duplicate task ``local_id`` rejected.

Reference scenario for the planner-validation subpackage. The planner emits a
full plan whose ``tasks`` list contains two entries sharing ``id="dup"``.
``build_planner_submission`` (``tools/submission/planner/_schemas.py``) rejects
the duplicate ids inside the planner submission tool; the orchestrator surfaces
this as a planner failure and closes the attempt with
``fail_reason="task_failed"``.

Both attempts in the iteration run the same invalid plan, so iteration 1 closes
with ``status=failed`` and the workflow closes ``status=failed``. No generator
or reducer task was created.

Asserts: ``report.request_status == "failed"``; graph state contains two
planner attempts, no accepted planner task, and no generator or reducer tasks.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _duplicate_local_id_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "dup", "agent_name": "executor", "needs": []},
            {"id": "dup", "agent_name": "executor", "needs": []},
        ],
        "task_specs": {"dup": "Run a workspace preflight."},
        "reducers": [
            {
                "id": "reduce",
                "needs": ["dup"],
                "prompt": "Confirm the task completed.",
            }
        ],
    }


class PlannerDuplicateLocalId(ScenarioBase):
    """Planner returns a duplicate-id plan; attempt closes task_failed."""

    name = "planner_validation.duplicate_local_id"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _duplicate_local_id_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        # Should never be invoked — both planner submissions are rejected
        # before the attempt reaches the reducer stage. The implementation
        # exists only so the scenario satisfies the protocol.
        return ToolCallSpec(
            submit_reducer_outcome,
            {"status": "failed", "outcome": "Unexpected reducer invocation under invalid plan."},
        )


__all__ = ["PlannerDuplicateLocalId"]
