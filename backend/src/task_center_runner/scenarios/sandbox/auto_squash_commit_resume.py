"""Auto-squash commit-resume probe — focused OCC mutation critical path.

Drives the public sandbox toolkit through ``AUTO_SQUASH_MAX_DEPTH + 4``
``write_file`` calls, then several ``edit_file`` calls, interleaved
``read_file`` checks, a ``shell`` readback, and one intentional
missing-anchor edit conflict. Captures every tool's timing metadata into a
sandbox summary artifact so the paired test can assert on
``occ.apply.commit_resume_wait_s``, ``layer_stack.auto_squash.total_s``, and
``layer_stack.auto_squash.depth_before > AUTO_SQUASH_MAX_DEPTH``.

This isolates the OCC mutation critical path that crosses
``AUTO_SQUASH_MAX_DEPTH`` and proves behavior equivalence while measuring
commit resume wait. Required gate per
``.omc/plans/occ-layer-stack-commit-resume-auto-squash-report-20260511.md``
before any non-synchronous squash path can become a default.
"""

from __future__ import annotations

from collections.abc import Sequence

from sandbox.occ.service import AUTO_SQUASH_MAX_DEPTH
from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_AUTO_SQUASH_WRITE_COUNT = AUTO_SQUASH_MAX_DEPTH + 4

def _auto_squash_plan() -> dict[str, object]:
    return {
        "plan_spec": (
            "Drive the sandbox toolkit through enough write/edit calls to cross "
            "the OCC auto-squash depth threshold while keeping at least two "
            "generator work streams active after seeding."
        ),
        "evaluation_criteria": [
            "Auto-squash is triggered naturally by public mutations.",
            "The sequential depth-building chain crosses the squash threshold.",
            "A disjoint independent generator runs alongside the depth chain.",
            "Final committed contents match across read_file and shell readback.",
            "Intentional missing-anchor edit reports a conflict with the same "
            "shape as the synchronous baseline.",
        ],
        "tasks": [
            {"id": "auto_squash_seed", "agent_name": "executor", "deps": []},
            {"id": "auto_squash_squash_a", "agent_name": "executor", "deps": ["auto_squash_seed"]},
            {"id": "auto_squash_independent", "agent_name": "executor", "deps": ["auto_squash_seed"]},
            {"id": "auto_squash_squash_b", "agent_name": "executor", "deps": ["auto_squash_squash_a"]},
            {
                "id": "auto_squash_reconcile",
                "agent_name": "executor",
                "deps": ["auto_squash_squash_b", "auto_squash_independent"],
            },
        ],
        "task_specs": {
            "auto_squash_seed": (
                "ACTION auto_squash_seed. Initialize directories for the "
                "auto-squash fan-out run."
            ),
            "auto_squash_squash_a": (
                "ACTION auto_squash_squash_a. Run the first sequential write "
                "slice for the depth-building chain."
            ),
            "auto_squash_independent": (
                "ACTION auto_squash_independent. Run disjoint read/write work "
                "concurrently with the depth-building chain."
            ),
            "auto_squash_squash_b": (
                "ACTION auto_squash_squash_b. Continue the depth-building "
                f"chain until {_AUTO_SQUASH_WRITE_COUNT} public writes cross "
                "the auto-squash threshold."
            ),
            "auto_squash_reconcile": (
                "ACTION auto_squash_reconcile. Aggregate write-slice "
                "fragments, perform post-threshold edits/readbacks, emit the "
                "intentional conflict, and write summary.json."
            ),
        },
    }


class AutoSquashCommitResume(ScenarioBase):
    """OCC mutation critical-path probe across AUTO_SQUASH_MAX_DEPTH."""

    name = "sandbox.auto_squash_commit_resume"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _auto_squash_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ctx.prompt or ""
        if "ACTION auto_squash_seed" in context_message:
            return ("auto_squash_seed",)
        if "ACTION auto_squash_squash_a" in context_message:
            return ("auto_squash_squash_a",)
        if "ACTION auto_squash_squash_b" in context_message:
            return ("auto_squash_squash_b",)
        if "ACTION auto_squash_independent" in context_message:
            return ("auto_squash_independent",)
        if "ACTION auto_squash_reconcile" in context_message:
            return ("auto_squash_reconcile",)
        return ()

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
