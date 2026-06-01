"""Complex project-build layer-stack projection scenario.

Drives a freshly-initialized ``/ephemeral-os`` workspace through a multi-phase
build (skeleton + patch progression + refactor passes + pytest + LSP
saturation + tri-source projection consistency + intentional conflicts + perf
metrics emission) per
``.omc/plans/complex-build-from-scratch-layer-stack-projection-verification-plan-20260511.md``.

Two scenario classes are exported:

- :class:`ComplexProjectBuild`: the full nightly form (~2,000+ tool calls,
  21 source files, 3 refactor passes, 14 pytest invocations, ≥30 calls per
  LSP tool).
- :class:`ComplexProjectBuildSmoke`: the pre-merge-gating smoke form (≥250
  tool calls, 6 source files, 1 refactor pass, 1 pytest invocation, ≥3
  calls per LSP tool).

Both run the same probe machinery in
``test_runner.agent.mock.complex_project_build_probe`` and emit a versioned perf
artifact at ``/ephemeral-os/.metrics/perf.json``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_REDUCER_PROMPT = (
    "Confirm the workspace was rebound to /ephemeral-os with pytest passing, "
    "all 5 LSP tools and the sandbox.api round-trip surface were exercised, "
    "auto-squash fired multiple times, tri-source projection agreed "
    "byte-for-byte, and the edit:write ratio met its floor."
)


_FULL_PLAN = {
    "tasks": [
        {
            "id": "complex_project_build",
            "agent_name": "executor",
            "needs": [],
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
    "reducers": [
        {
            "id": "reduce",
            "needs": ["complex_project_build"],
            "prompt": _REDUCER_PROMPT,
        }
    ],
}


_SMOKE_PLAN = {
    "tasks": [
        {
            "id": "complex_project_build_smoke",
            "agent_name": "executor",
            "needs": [],
        },
    ],
    "task_specs": {
        "complex_project_build_smoke": (
            "Smoke complex project-build: 6 source files, 1 refactor pass, "
            "1 pytest invocation, ≥250 tool calls."
        ),
    },
    "reducers": [
        {
            "id": "reduce",
            "needs": ["complex_project_build_smoke"],
            "prompt": _REDUCER_PROMPT,
        }
    ],
}


class ComplexProjectBuild(ScenarioBase):
    """Full nightly form of the complex project-build scenario."""

    name = "sandbox.complex_project_build"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, dict(_FULL_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Complex project build under /ephemeral-os exercised the "
                    "layer-stack/overlay/OCC stack end-to-end with pytest "
                    "passing through the projected workspace."
                ),
            },
        )


class ComplexProjectBuildSmoke(ScenarioBase):
    """Smoke variant for pre-merge gating — same probe, smaller fixture set."""

    name = "sandbox.complex_project_build_smoke"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, dict(_SMOKE_PLAN))

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("complex_project_build_smoke",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Smoke complex project build under /ephemeral-os "
                    "exercised the layer-stack/overlay/OCC stack with pytest "
                    "passing through the projected workspace."
                ),
            },
        )


__all__ = ["ComplexProjectBuild", "ComplexProjectBuildSmoke"]
