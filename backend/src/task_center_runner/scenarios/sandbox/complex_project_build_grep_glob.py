"""Complex project-build scenarios exercising grep, glob, and edit_file."""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_FULL_PLAN = {
    "plan_spec": (
        "Build the scheduler_demo project under /ephemeral-os using a heavy "
        "structured search/edit workload: glob must enumerate candidate files, "
        "grep must locate anchors before and after edits, edit_file must apply "
        "the mutations, and the full run must drive at least 2000 toolkit tool "
        "calls."
    ),
    "evaluation_criteria": [
        "Workspace base is rebound to /ephemeral-os and pytest runs there.",
        "`glob` is used repeatedly to enumerate exact files and Python file sets.",
        "`grep` is used repeatedly in files_with_matches, count, and content modes.",
        "`edit_file` mutations are bracketed by grep/glob verification.",
        "The heavy full run records at least 2000 toolkit tool calls.",
        "Final pytest exit code is 0.",
        "Tri-source projection (read_file == shell cat == sandbox.api) agrees byte-for-byte.",
        "The emitted summary/perf artifacts include grep_glob counters.",
    ],
    "tasks": [
        {
            "id": "complex_project_build_grep_glob",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "complex_project_build_grep_glob": (
            "Run the heavy grep + glob + edit_file project-build probe under "
            "/ephemeral-os and emit /ephemeral-os/.metrics/perf.json plus "
            "/ephemeral-os/.metrics/summary.json."
        ),
    },
}


_SMOKE_PLAN = {
    "plan_spec": (
        "Smoke variant of the grep + glob workflow probe. The executor uses "
        "`glob` to find candidate files, `grep` to locate a known anchor, "
        "`edit_file` to replace the anchor, and a final read or grep to "
        "verify the replacement landed."
    ),
    "evaluation_criteria": [
        "At least one `glob` call returns the candidate Python files.",
        "At least one `grep` call locates the anchor string before the edit.",
        "The edit replaces the anchor with the target string.",
        "A follow-up read or `grep` confirms the new string is present.",
    ],
    "tasks": [
        {
            "id": "complex_project_build_grep_glob_smoke",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "complex_project_build_grep_glob_smoke": (
            "Run the smoke grep + glob workflow probe under /ephemeral-os: "
            "enumerate Python files via `glob`, locate the documented anchor "
            "via `grep`, replace it with the target string via `edit_file`, "
            "and verify the replacement via a follow-up read or `grep`."
        ),
    },
}


_EXPECTED_EVENT_SEQUENCE: tuple[EventType, ...] = (
    EventType.PLANNER_INVOKED,
    EventType.PLANNER_COMPLETES_GOAL_PLAN,
    EventType.EXECUTOR_INVOKED,
    EventType.EXECUTOR_SUCCESS,
    EventType.EVALUATOR_INVOKED,
    EventType.EVALUATOR_SUCCESS,
)


class ComplexProjectBuildGrepGlob(ScenarioBase):
    """Full heavy grep + glob + edit_file project-build scenario."""

    name = "sandbox.complex_project_build_grep_glob"
    expected_event_sequence: tuple[EventType, ...] = _EXPECTED_EVENT_SEQUENCE

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, dict(_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_grep_glob",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Heavy grep + glob + edit_file project-build probe under "
                    "/ephemeral-os completed with pytest and projection checks passing."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


class ComplexProjectBuildGrepGlobSmoke(ScenarioBase):
    """Smoke variant of the grep + glob workflow scenario."""

    name = "sandbox.complex_project_build_grep_glob_smoke"
    expected_event_sequence: tuple[EventType, ...] = _EXPECTED_EVENT_SEQUENCE

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, dict(_SMOKE_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_grep_glob_smoke",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Grep + glob workflow probe under /ephemeral-os completed: "
                    "candidate files enumerated, anchor located, edit applied, "
                    "and replacement verified."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["ComplexProjectBuildGrepGlob", "ComplexProjectBuildGrepGlobSmoke"]
