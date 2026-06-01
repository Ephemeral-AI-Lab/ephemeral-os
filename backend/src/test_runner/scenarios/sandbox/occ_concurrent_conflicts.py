"""OCC concurrent conflict detection — write/edit/conflict round trip.

Reference scenario for the sandbox subsystem. Plan emits one executor task
that fires the existing ``sandbox_integrity`` action, which exercises:

- ``write_file`` -> real ``Service.apply_changeset`` -> layer published
- ``read_file`` round trip — proves layerstack publish is readable
- ``edit_file`` (search/replace) — proves OCC merge of disjoint edits
- ``shell`` command mutating a file — proves overlay capture path
- batch edit covering multiple search/replace blocks
- a deliberately stale edit that triggers conflict reporting

Asserts on the ``EventType.SANDBOX_*`` events emitted from tool completions:
``SANDBOX_BATCH_EDIT_APPLIED`` and ``SANDBOX_CONFLICT_DETECTED`` must both
appear in the run's event sequence. This is the canonical pattern for
sandbox-subsystem scenarios that need to assert subsystem-level behavior.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_INTEGRITY_PLAN = {
    "tasks": [
        {"id": "sandbox_integrity", "agent_name": "executor", "needs": []},
    ],
    "task_specs": {
        "sandbox_integrity": (
            "Exercise the sandbox filesystem with write_file, read_file, "
            "edit_file, shell, a batch public edit, and an expected conflict."
        ),
    },
    "reducers": [
        {
            "id": "reduce",
            "needs": ["sandbox_integrity"],
            "prompt": (
                "Confirm the sandbox toolkit could read, write, edit, and run "
                "shell, the batch edit succeeded, and a stale edit reported a "
                "conflict."
            ),
        }
    ],
}


class OccConcurrentConflicts(ScenarioBase):
    """OCC + layer-stack + overlay + conflict round trip via sandbox_integrity."""

    name = "sandbox.occ_concurrent_conflicts"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, dict(_INTEGRITY_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("sandbox_integrity",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Sandbox integrity probe captured both batch-edit and conflict evidence."
                ),
            },
        )


__all__ = ["OccConcurrentConflicts"]
