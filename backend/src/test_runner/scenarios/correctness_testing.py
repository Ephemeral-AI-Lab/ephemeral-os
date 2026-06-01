"""correctness_testing scenario — the only scenario shipped this phase.

One composite that exercises entry → workflow → iteration 1 (attempt 1 fails;
attempt 2 passes via partial plan) → continuation iteration (attempt 1 passes
via full plan + final probe) → workflow close. Validates the full happy path
plus one failure path per plan §10.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from task_center_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)


_PREFLIGHT_FULL_PLAN: dict = {
    "tasks": [{"id": "preflight", "agent_name": "executor", "needs": []}],
    "task_specs": {
        "preflight": (
            "Run a lightweight workspace preflight and report the observed sandbox root."
        ),
    },
    "reducers": [
        {
            "id": "reduce",
            "needs": ["preflight"],
            "prompt": (
                "Confirm workspace preflight completed and the retry path was "
                "exercised by reducer feedback."
            ),
        }
    ],
}

_INTEGRITY_PARTIAL_PLAN: dict = {
    "tasks": [
        {
            "id": "sandbox_integrity",
            "agent_name": "executor",
            "needs": [],
        },
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
                "Confirm sandbox read/write/edit/shell consistency, that the "
                "final file content survives the shell/OCC squash boundary, and "
                "that a stale edit reports conflict."
            ),
        }
    ],
    "deferred_goal_for_next_iteration": (
        "Run the final SWE-EVO mock grading iteration after sandbox integrity "
        "evidence has been persisted."
    ),
}

_FINAL_PROBE_FULL_PLAN: dict = {
    "tasks": [{"id": "final_probe", "agent_name": "executor", "needs": []}],
    "task_specs": {
        "final_probe": (
            "Read the sandbox integrity artifact and verify the final "
            "squash marker is still present."
        ),
    },
    "reducers": [
        {
            "id": "reduce",
            "needs": ["final_probe"],
            "prompt": (
                "Confirm the continuation iteration received previous-iteration "
                "context and the persisted sandbox evidence is readable."
            ),
        }
    ],
}


class CorrectnessTesting(ScenarioBase):
    """Single composite scenario validating framework end-to-end."""

    name = "correctness_testing"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        iteration = ctx.iteration
        attempt = ctx.attempt
        if iteration.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_planner_outcome, dict(_PREFLIGHT_FULL_PLAN))
        if iteration.sequence_no == 1:
            return ToolCallSpec(submit_planner_outcome, dict(_INTEGRITY_PARTIAL_PLAN))
        return ToolCallSpec(submit_planner_outcome, dict(_FINAL_PROBE_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        prompt = ctx.instruction or ctx.prompt or ""
        if "sandbox filesystem" in prompt or "sandbox read/write/edit" in prompt:
            return ("sandbox_integrity",)
        if "squash marker" in prompt:
            return ("final_probe",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        iteration = ctx.iteration
        attempt = ctx.attempt
        if iteration.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(
                submit_reducer_outcome,
                {
                    "status": "failed",
                    "outcome": (
                        "Intentional mock failure to verify iteration retry and "
                        "failed-attempt context."
                    ),
                },
            )
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": "Mock reducer accepted the current attempt evidence.",
            },
        )


__all__ = ["CorrectnessTesting"]
