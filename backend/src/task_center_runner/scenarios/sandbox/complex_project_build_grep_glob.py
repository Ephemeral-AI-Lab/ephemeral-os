"""Complex project-build scenario exercising the new grep and glob tools.

The scenario asks the planner-executor-evaluator triad to drive an end-to-end
workspace exploration where the executor uses ``glob`` to enumerate Python
files, ``grep`` to locate a known anchor across them, ``edit_file`` to replace
the anchor, and a follow-up read to verify the edit landed. Live-only — the
matching pytest test is gated on EPHEMERALOS_DATABASE_URL.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_full_plan

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_SMOKE_PLAN = {
    "task_specification": (
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
    EventType.ENTRY_EXECUTOR_INVOKED,
    EventType.PLANNER_INVOKED,
    EventType.PLANNER_FULL_PLAN,
    EventType.EXECUTOR_INVOKED,
    EventType.EXECUTOR_SUCCESS,
    EventType.EVALUATOR_INVOKED,
    EventType.EVALUATOR_SUCCESS,
)


class ComplexProjectBuildGrepGlobSmoke(ScenarioBase):
    """Smoke variant of the grep + glob workflow scenario."""

    name = "sandbox.complex_project_build_grep_glob_smoke"
    expected_event_sequence: tuple[EventType, ...] = _EXPECTED_EVENT_SEQUENCE

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_full_plan, dict(_SMOKE_PLAN))

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


__all__ = ["ComplexProjectBuildGrepGlobSmoke"]
