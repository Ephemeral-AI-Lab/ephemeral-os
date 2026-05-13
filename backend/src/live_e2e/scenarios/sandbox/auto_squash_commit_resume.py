"""Auto-squash commit-resume probe — focused OCC mutation critical path.

Drives the public sandbox toolkit through ``AUTO_SQUASH_MAX_DEPTH + 4``
``write_file`` calls, then several ``edit_file`` calls, interleaved
``read_file`` checks, a ``shell`` readback, and one intentional
missing-anchor edit conflict. Captures every tool's timing metadata into a
sandbox summary artifact so the paired test can assert on
``occ.apply.commit_resume_wait_s``, ``layer_stack.auto_squash.total_s``, and
``layer_stack.auto_squash.depth_before > 32``.

This isolates the OCC mutation critical path that crosses
``AUTO_SQUASH_MAX_DEPTH`` and proves behavior equivalence while measuring
commit resume wait. Required gate per
``.omc/plans/occ-layer-stack-commit-resume-auto-squash-report-20260511.md``
before any non-synchronous squash path can become a default.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_full_plan

from live_e2e.audit.events import EventType
from live_e2e.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_AUTO_SQUASH_PLAN = {
    "task_specification": (
        "Drive the sandbox toolkit through enough write/edit calls to cross "
        "the OCC auto-squash depth threshold and capture commit-resume "
        "timing evidence."
    ),
    "evaluation_criteria": [
        "Auto-squash is triggered naturally by public mutations.",
        "Final committed contents match across read_file and shell readback.",
        "Intentional missing-anchor edit reports a conflict with the same "
        "shape as the synchronous baseline.",
    ],
    "tasks": [
        {
            "id": "auto_squash_commit_resume_probe",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "auto_squash_commit_resume_probe": (
            "Run the auto-squash commit-resume probe: 36 sequential writes "
            "to cross the depth threshold, post-threshold edits, interleaved "
            "reads, a shell readback, and one intentional missing-anchor "
            "edit conflict."
        ),
    },
}


class AutoSquashCommitResume(ScenarioBase):
    """OCC mutation critical-path probe across AUTO_SQUASH_MAX_DEPTH."""

    name = "sandbox.auto_squash_commit_resume"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.SANDBOX_CONFLICT_DETECTED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_full_plan, dict(_AUTO_SQUASH_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("auto_squash_commit_resume_probe",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Auto-squash commit-resume probe captured depth-crossing "
                    "writes, post-threshold edits, intentional conflict, and "
                    "final readback agreement."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["AutoSquashCommitResume"]
