"""Complex project-build layer-stack projection scenario.

Drives a freshly-initialized ``/ephemeral-os`` git repository through a
multi-phase build (skeleton + patch progression + refactor passes + pytest +
LSP saturation + tri-source projection consistency + intentional conflicts +
perf metrics emission) per
``.omc/plans/complex-build-from-scratch-layer-stack-projection-verification-plan-20260511.md``.

Two scenario classes are exported:

- :class:`ComplexProjectBuild`: the full nightly form (~2,000+ tool calls,
  21 source files, 3 refactor passes, 14 pytest invocations, ≥30 calls per
  LSP tool).
- :class:`ComplexProjectBuildSmoke`: the pre-merge-gating smoke form (≥250
  tool calls, 6 source files, 1 refactor pass, 1 pytest invocation, ≥3
  calls per LSP tool).

Both run the same probe machinery in
``task_center_runner.agent.mock.complex_project_build_probe`` and emit a versioned perf
artifact at ``/ephemeral-os/.metrics/perf.json``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_FULL_PLAN = {
    "task_specification": (
        "Build a small stdlib-only Python scheduler library inside a freshly "
        "initialized /ephemeral-os git repo, exercising the layer stack, "
        "overlay capture, OCC apply path, and Pyright LSP across many files."
    ),
    "evaluation_criteria": [
        "Workspace base is rebound to /ephemeral-os and pytest runs there.",
        "All 5 LSP tools and the direct sandbox.api round-trip surface are "
        "exercised.",
        "Auto-squash fires multiple times across the run.",
        "Final pytest exit code is 0.",
        "Tri-source projection (read_file == shell cat == sandbox.api) agrees "
        "byte-for-byte.",
        "Edit:write ratio >= 4x.",
    ],
    "tasks": [
        {
            "id": "complex_project_build",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "complex_project_build": (
            "Run the complex project-build probe: phases 0..F producing the "
            "scheduler_demo project under /ephemeral-os, with skeleton + "
            "patch progression, refactor passes, pytest gate, LSP saturation, "
            "tri-source projection consistency, intentional conflicts, and "
            "/ephemeral-os/.metrics/perf.json emission."
        ),
    },
}


_SMOKE_PLAN = {
    "task_specification": (
        "Smoke variant of the complex project-build probe — covers the same "
        "phases but with a smaller fixture set so it can run pre-merge."
    ),
    "evaluation_criteria": list(_FULL_PLAN["evaluation_criteria"]),
    "tasks": [
        {
            "id": "complex_project_build_smoke",
            "agent_name": "executor",
            "deps": [],
        },
    ],
    "task_specs": {
        "complex_project_build_smoke": (
            "Smoke complex project-build: 6 source files, 1 refactor pass, "
            "1 pytest invocation, ≥250 tool calls."
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


class ComplexProjectBuild(ScenarioBase):
    """Full nightly form of the complex project-build scenario."""

    name = "sandbox.complex_project_build"
    expected_event_sequence: tuple[EventType, ...] = _EXPECTED_EVENT_SEQUENCE

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, dict(_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Complex project build under /ephemeral-os exercised the "
                    "layer-stack/overlay/OCC stack end-to-end with pytest "
                    "passing through the projected workspace."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


class ComplexProjectBuildSmoke(ScenarioBase):
    """Smoke variant for pre-merge gating — same probe, smaller fixture set."""

    name = "sandbox.complex_project_build_smoke"
    expected_event_sequence: tuple[EventType, ...] = _EXPECTED_EVENT_SEQUENCE

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, dict(_SMOKE_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_smoke",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Smoke complex project build under /ephemeral-os "
                    "exercised the layer-stack/overlay/OCC stack with pytest "
                    "passing through the projected workspace."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["ComplexProjectBuild", "ComplexProjectBuildSmoke"]
