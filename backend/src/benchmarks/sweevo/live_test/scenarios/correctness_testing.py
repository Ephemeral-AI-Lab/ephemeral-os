"""correctness_testing scenario — the only scenario shipped this phase.

One composite that exercises entry → mission → episode 1 (attempt 1 fails;
attempt 2 passes via partial plan) → continuation episode (attempt 1 passes
via full plan + final probe) → mission close. Validates the full happy path
plus one failure path per plan §10.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.main_agent.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.main_agent.planner import (
    submit_full_plan,
    submit_partial_plan,
)

from benchmarks.sweevo.live_test.audit.events import EventType
from benchmarks.sweevo.live_test.hooks.registry import Hook
from benchmarks.sweevo.live_test.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)


_PREFLIGHT_FULL_PLAN: dict = {
    "task_specification": (
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
    "task_specification": (
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
    "continuation_goal": (
        "Run the final SWE-EVO mock grading episode after sandbox integrity "
        "evidence has been persisted."
    ),
}

_FINAL_PROBE_FULL_PLAN: dict = {
    "task_specification": (
        "Confirm the sandbox integrity artifacts remain readable in the "
        "continuation episode and close the benchmark mission."
    ),
    "evaluation_criteria": [
        "Continuation episode received previous episode context.",
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
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_FAILURE,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_PARTIAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.SANDBOX_BATCH_EDIT_APPLIED,
        EventType.SANDBOX_CONFLICT_DETECTED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        episode = ctx.episode
        attempt = ctx.attempt
        if episode.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_full_plan, dict(_PREFLIGHT_FULL_PLAN))
        if episode.sequence_no == 1:
            return ToolCallSpec(submit_partial_plan, dict(_INTEGRITY_PARTIAL_PLAN))
        return ToolCallSpec(submit_full_plan, dict(_FINAL_PROBE_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        prompt = ctx.prompt or ""
        if "sandbox filesystem" in prompt or "sandbox read/write/edit" in prompt:
            return ("sandbox_integrity",)
        if "squash marker" in prompt:
            return ("final_probe",)
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        episode = ctx.episode
        attempt = ctx.attempt
        if episode.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(
                submit_evaluation_failure,
                {
                    "summary": (
                        "Intentional mock failure to verify episode retry and "
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
