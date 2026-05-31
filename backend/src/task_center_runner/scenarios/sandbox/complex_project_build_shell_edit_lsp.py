"""Complex project-build scenario with shell edits and semantic LSP checks."""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_REDUCER_PROMPT = (
    "Confirm the workspace was rebound to /ephemeral-os with pytest passing, "
    "edits were routed deterministically with the shell-edit ratio near one "
    "third, each LSP tool contributed its semantic-check floor and passed, "
    "diagnostics detected then cleared the broken probe file, and tri-source "
    "projection agreed byte-for-byte."
)


_FULL_PLAN = {
    "tasks": [
        {
            "id": "complex_project_build_shell_edit_lsp",
            "agent_name": "executor",
            "needs": [],
        },
    ],
    "task_specs": {
        "complex_project_build_shell_edit_lsp": (
            "Run the mixed shell-edit + LSP saturation project-build probe "
            "under /ephemeral-os and emit /ephemeral-os/.metrics/perf.json "
            "plus /ephemeral-os/.metrics/summary.json."
        ),
    },
    "reducers": [
        {
            "id": "reduce",
            "needs": ["complex_project_build_shell_edit_lsp"],
            "prompt": _REDUCER_PROMPT,
        }
    ],
}


_SMOKE_PLAN = {
    "tasks": [
        {
            "id": "complex_project_build_shell_edit_lsp_smoke",
            "agent_name": "executor",
            "needs": [],
        },
    ],
    "task_specs": {
        "complex_project_build_shell_edit_lsp_smoke": (
            "Run the smoke mixed shell-edit + LSP saturation project-build "
            "probe under /ephemeral-os."
        ),
    },
    "reducers": [
        {
            "id": "reduce",
            "needs": ["complex_project_build_shell_edit_lsp_smoke"],
            "prompt": _REDUCER_PROMPT,
        }
    ],
}


class ComplexProjectBuildShellEditLsp(ScenarioBase):
    """Full mixed shell-edit + semantic LSP project-build scenario."""

    name = "sandbox.complex_project_build_shell_edit_lsp"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, dict(_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_shell_edit_lsp",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Mixed shell-edit + LSP project build under /ephemeral-os "
                    "passed pytest and semantic sandbox checks."
                ),
            },
        )


class ComplexProjectBuildShellEditLspSmoke(ScenarioBase):
    """Smoke variant of the mixed shell-edit + semantic LSP scenario."""

    name = "sandbox.complex_project_build_shell_edit_lsp_smoke"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, dict(_SMOKE_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_shell_edit_lsp_smoke",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Smoke mixed shell-edit + LSP project build under "
                    "/ephemeral-os passed pytest and semantic sandbox checks."
                ),
            },
        )


__all__ = [
    "ComplexProjectBuildShellEditLsp",
    "ComplexProjectBuildShellEditLspSmoke",
]
