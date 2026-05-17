"""Complex project-build scenario with shell edits and semantic LSP checks."""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_FULL_PLAN = {
    "plan_spec": (
        "Build the scheduler_demo project under /ephemeral-os using a mixed "
        "edit workload: roughly one third shell-based file mutations, the "
        "remaining edits through edit_file, and at least 200 semantic LSP "
        "checks across hover, definitions, references, symbols, and diagnostics."
    ),
    "evaluation_criteria": [
        "Workspace base is rebound to /ephemeral-os and pytest runs there.",
        "Logical edits are routed deterministically with every third edit "
        "using shell-based mutation.",
        "Shell edit ratio is within three percentage points of one third.",
        "Each LSP tool contributes at least 40 semantic checks and all pass.",
        "Diagnostics detect the intentionally broken probe file and clear "
        "after repair.",
        "Tri-source projection (read_file == shell cat == sandbox.api) agrees "
        "byte-for-byte.",
    ],
    "tasks": [
        {
            "id": "complex_project_build_shell_edit_lsp",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "complex_project_build_shell_edit_lsp": (
            "Run the mixed shell-edit + LSP saturation project-build probe "
            "under /ephemeral-os and emit /ephemeral-os/.metrics/perf.json "
            "plus /ephemeral-os/.metrics/summary.json."
        ),
    },
}


_SMOKE_PLAN = {
    "plan_spec": (
        "Smoke variant of the mixed shell-edit + LSP saturation project-build "
        "probe with the same routing rule and reduced edit/LSP floors."
    ),
    "evaluation_criteria": list(_FULL_PLAN["evaluation_criteria"]),
    "tasks": [
        {
            "id": "complex_project_build_shell_edit_lsp_smoke",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "complex_project_build_shell_edit_lsp_smoke": (
            "Run the smoke mixed shell-edit + LSP saturation project-build "
            "probe under /ephemeral-os."
        ),
    },
}


_EXPECTED_EVENT_SEQUENCE: tuple[EventType, ...] = (
    EventType.ENTRY_EXECUTOR_INVOKED,
    EventType.PLANNER_INVOKED,
    EventType.PLANNER_FULL_PLAN,
    EventType.EXECUTOR_INVOKED,
    EventType.SANDBOX_CONFLICT_DETECTED,
    EventType.EXECUTOR_SUCCESS,
    EventType.EVALUATOR_INVOKED,
    EventType.EVALUATOR_SUCCESS,
)


class ComplexProjectBuildShellEditLsp(ScenarioBase):
    """Full mixed shell-edit + semantic LSP project-build scenario."""

    name = "sandbox.complex_project_build_shell_edit_lsp"
    expected_event_sequence: tuple[EventType, ...] = _EXPECTED_EVENT_SEQUENCE

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, dict(_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_shell_edit_lsp",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Mixed shell-edit + LSP project build under /ephemeral-os "
                    "passed pytest and semantic sandbox checks."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


class ComplexProjectBuildShellEditLspSmoke(ScenarioBase):
    """Smoke variant of the mixed shell-edit + semantic LSP scenario."""

    name = "sandbox.complex_project_build_shell_edit_lsp_smoke"
    expected_event_sequence: tuple[EventType, ...] = _EXPECTED_EVENT_SEQUENCE

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, dict(_SMOKE_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_shell_edit_lsp_smoke",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Smoke mixed shell-edit + LSP project build under "
                    "/ephemeral-os passed pytest and semantic sandbox checks."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = [
    "ComplexProjectBuildShellEditLsp",
    "ComplexProjectBuildShellEditLspSmoke",
]
