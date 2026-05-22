"""Background-shell scenarios for ``shell(background=True)`` validation.

Three minimal scenarios registered in
``task_center_runner.scenarios.SCENARIO_REGISTRY``:

- ``sandbox.background_shell_golden``
- ``sandbox.background_shell_cancel``
- ``sandbox.background_shell_interleave``

**Design note.** The Phase 2 live integration tests (T1, T2, T3) drive
the daemon's job-control surface directly via
:mod:`task_center_runner.agent.mock.background_shell_probe`, NOT via the
mock-agent + scenario machinery (see the probe docstring for the design
rationale). These scenario classes exist primarily to satisfy the PRD
US-005 acceptance criterion that names them in ``SCENARIO_REGISTRY`` —
they are valid ``ScenarioBase`` subclasses but the live tests don't go
through them.

A future phase could wire ``tools.background.*`` through the mock-agent
harness; at that point these scenarios become first-class entry points
for an agent-driven background-shell e2e.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)


def _plan(action_id: str, action_spec: str) -> dict[str, Any]:
    return {
        "plan_spec": (
            f"Single-task plan for the {action_id} background-shell scenario."
        ),
        "evaluation_criteria": [
            "Probe completed its background-shell workload.",
            "Daemon's job-control RPCs (launch / poll / cancel / reap) "
            "produced consistent audit events.",
        ],
        "tasks": [
            {"id": action_id, "agent_name": "executor", "deps": []},
        ],
        "task_specs": {action_id: action_spec},
    }


class _BackgroundShellScenarioBase(ScenarioBase):
    """Shared planner/executor/evaluator shape for the 3 background-shell scenarios."""

    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    action_id: str = ""
    action_spec: str = ""

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_plan_closes_goal,
            _plan(self.action_id, self.action_spec),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[Any]:
        context_message = ctx.context_message or ctx.prompt or ""
        if f"ACTION {self.action_id}" in context_message:
            return (self.action_id,)
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    f"{self.action_id} background-shell scenario completed."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


class BackgroundShellGolden(_BackgroundShellScenarioBase):
    """N concurrent ``shell(background=True)`` launches, all reach ``finished``."""

    name = "sandbox.background_shell_golden"
    action_id = "background_shell_golden"
    action_spec = (
        "ACTION background_shell_golden. Launch N concurrent background "
        "shells via tools.sandbox.shell with background=true, wait for "
        "natural exit, and report the per-launch summary."
    )


class BackgroundShellCancel(_BackgroundShellScenarioBase):
    """Launch background shells; cancel mid-flight; assert no leftover state."""

    name = "sandbox.background_shell_cancel"
    action_id = "background_shell_cancel"
    action_spec = (
        "ACTION background_shell_cancel. Launch N long-running background "
        "shells, cancel each mid-flight via tools.background.cancel_background_task, "
        "and verify the workspace OCC is unchanged."
    )


class BackgroundShellInterleave(_BackgroundShellScenarioBase):
    """1 background + M interleaved foreground shells; foreground p95 mount unchanged."""

    name = "sandbox.background_shell_interleave"
    action_id = "background_shell_interleave"
    action_spec = (
        "ACTION background_shell_interleave. Launch 1 long-running "
        "background shell, run M interleaved foreground shells, and "
        "record foreground mount-latency timings for AC-3."
    )


__all__ = [
    "BackgroundShellCancel",
    "BackgroundShellGolden",
    "BackgroundShellInterleave",
]
