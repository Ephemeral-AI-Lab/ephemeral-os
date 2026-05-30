"""correctness_testing scenario — the only scenario shipped this phase.

One composite that exercises entry → workflow → iteration 1 (attempt 1 fails;
attempt 2 passes via partial plan) → continuation iteration (attempt 1 passes
via full plan + final probe) → workflow close. Validates the full happy path
plus one failure path per plan §10.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.planner import (
    submit_plan_closes_goal,
    submit_plan_defers_goal,
)

from task_center_runner.audit.events import EventType
from task_center_runner.hooks.registry import Hook
from task_center_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)


_PREFLIGHT_FULL_PLAN: dict = {
    "plan_spec": (
        "Preflight the SWE-EVO workspace and expose an evaluator retry signal "
        "without making benchmark source edits."
    ),
    "evaluation_criteria": [
        "Workspace preflight completed.",
        "Retry path was exercised by evaluator feedback.",
    ],
    "tasks": [{"id": "preflight", "agent_name": "executor", "deps": []}],
    "task_specs": {
        "preflight": (
            "Run a lightweight workspace preflight and report the observed "
            "sandbox root."
        ),
    },
}

_INTEGRITY_PARTIAL_PLAN: dict = {
    "plan_spec": (
        "Validate sandbox read/write/edit/shell consistency, direct OCC file "
        "mutation, gated shell mutation, batch edit handling, and conflict "
        "reporting for the SWE-EVO workspace."
    ),
    "evaluation_criteria": [
        "Dedicated sandbox tools can read, write, edit, and run shell.",
        "Final file content survives the shell/OCC squash boundary.",
        "Batch edit succeeds and a stale edit reports conflict.",
    ],
    "tasks": [
        {
            "id": "sandbox_integrity",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "sandbox_integrity": (
            "Exercise the sandbox filesystem with write_file, read_file, "
            "edit_file, shell, a batch public edit, and an expected conflict."
        ),
    },
    "deferred_goal_for_next_iteration": (
        "Run the final SWE-EVO mock grading iteration after sandbox integrity "
        "evidence has been persisted."
    ),
}

_FINAL_PROBE_FULL_PLAN: dict = {
    "plan_spec": (
        "Confirm the sandbox integrity artifacts remain readable in the "
        "continuation iteration and close the benchmark workflow."
    ),
    "evaluation_criteria": [
        "Continuation iteration received previous iteration context.",
        "Persisted sandbox evidence is readable from the workspace.",
    ],
    "tasks": [{"id": "final_probe", "agent_name": "executor", "deps": []}],
    "task_specs": {
        "final_probe": (
            "Read the sandbox integrity artifact and verify the final "
            "squash marker is still present."
        ),
    },
}


class CorrectnessTesting(ScenarioBase):
    """Single composite scenario validating framework end-to-end."""

    name = "correctness_testing"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_FAILURE,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_DEFERS_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.SANDBOX_BATCH_EDIT_APPLIED,
        EventType.SANDBOX_CONFLICT_DETECTED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        iteration = ctx.iteration
        attempt = ctx.attempt
        if iteration.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_plan_closes_goal, dict(_PREFLIGHT_FULL_PLAN))
        if iteration.sequence_no == 1:
            return ToolCallSpec(submit_plan_defers_goal, dict(_INTEGRITY_PARTIAL_PLAN))
        return ToolCallSpec(submit_plan_closes_goal, dict(_FINAL_PROBE_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        prompt = ctx.context_message or ctx.prompt or ""
        if "sandbox filesystem" in prompt or "sandbox read/write/edit" in prompt:
            return ("sandbox_integrity",)
        if "squash marker" in prompt:
            return ("final_probe",)
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        iteration = ctx.iteration
        attempt = ctx.attempt
        if iteration.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(
                submit_evaluation_failure,
                {
                    "summary": (
                        "Intentional mock failure to verify iteration retry and "
                        "failed-attempt context."
                    ),
                    "failed_criteria": [
                        "Retry path was exercised by evaluator feedback.",
                    ],
                },
            )
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": "Mock evaluator accepted the current attempt evidence.",
                "passed_criteria": list(attempt.evaluation_criteria),
            },
        )

    def hooks(self) -> Sequence[Hook]:
        return ()


__all__ = ["CorrectnessTesting"]
