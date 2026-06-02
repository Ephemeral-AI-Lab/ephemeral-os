"""Complex project-build scenarios exercising grep, glob, and edit_file."""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_FULL_PLAN = {
    "tasks": [
        {
            "id": "complex_project_build_grep_glob",
            "agent_name": "executor",
            "needs": [],
        },
    ],
    "task_specs": {
        "complex_project_build_grep_glob": (
            "Run the heavy grep + glob + edit_file project-build probe under "
            "/ephemeral-os and emit /ephemeral-os/.metrics/perf.json plus "
            "/ephemeral-os/.metrics/summary.json."
        ),
    },
    "reducers": [
        {
            "id": "reduce",
            "needs": ["complex_project_build_grep_glob"],
            "prompt": (
                "Confirm glob/grep enumeration and edit_file mutations were "
                "bracketed by verification, the heavy run drove >=2000 tool "
                "calls, pytest passed, tri-source projection agreed, and the "
                "artifacts include grep_glob counters."
            ),
        }
    ],
}


_SMOKE_PLAN = {
    "tasks": [
        {
            "id": "complex_project_build_grep_glob_smoke",
            "agent_name": "executor",
            "needs": [],
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
    "reducers": [
        {
            "id": "reduce",
            "needs": ["complex_project_build_grep_glob_smoke"],
            "prompt": (
                "Confirm glob enumerated candidate files, grep located the "
                "anchor before the edit, edit_file replaced it, and a "
                "follow-up read or grep confirmed the new string is present."
            ),
        }
    ],
}


class ComplexProjectBuildGrepGlob(ScenarioBase):
    """Full heavy grep + glob + edit_file project-build scenario."""

    name = "sandbox.complex_project_build_grep_glob"
    delegated_workflow_poll_attempts = 180
    delegated_workflow_poll_interval_s = 3.0

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, dict(_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_grep_glob",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Heavy grep + glob + edit_file project-build probe under "
                    "/ephemeral-os completed with pytest and projection checks passing."
                ),
            },
        )


class ComplexProjectBuildGrepGlobSmoke(ScenarioBase):
    """Smoke variant of the grep + glob workflow scenario."""

    name = "sandbox.complex_project_build_grep_glob_smoke"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, dict(_SMOKE_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_grep_glob_smoke",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Grep + glob workflow probe under /ephemeral-os completed: "
                    "candidate files enumerated, anchor located, edit applied, "
                    "and replacement verified."
                ),
            },
        )


__all__ = ["ComplexProjectBuildGrepGlob", "ComplexProjectBuildGrepGlobSmoke"]
