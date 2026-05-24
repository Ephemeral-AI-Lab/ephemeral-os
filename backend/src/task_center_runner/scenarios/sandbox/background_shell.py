"""Background-shell scenarios that drive ``shell(background=True)`` through the
engine-owned background task harness.

Seven scenarios (T1-T3, T5-T8 from the Phase 2 plan; T4 is covered by the
invocation-keyed daemon in-flight TTL tests):

- ``sandbox.background_shell_golden`` (T1)
- ``sandbox.background_shell_stop`` (T2)
- ``sandbox.background_shell_interleave`` (T3)
- ``sandbox.background_shell_exhaustion`` (T5)
- ``sandbox.background_shell_partial_write_cancel`` (T6)
- ``sandbox.background_shell_stop_during_maintenance`` (T7)
- ``sandbox.background_shell_late_cancel_race`` (T8)

Each scenario uses a single executor action that drives the matching
probe in :mod:`task_center_runner.agent.mock.background_shell_probe`.
The probes call the shell tool with ``background_task_id`` set so the
tool framework keeps the request correlated with the engine background task;
the harness records full ``sandbox_events.jsonl`` plus
``performance_report.json`` artifacts.
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


def _plan(action_id: str, action_spec: str, summary_hint: str) -> dict[str, Any]:
    return {
        "plan_spec": (
            f"Single-task plan that drives the {action_id} background-shell "
            "probe through the mock-agent harness."
        ),
        "evaluation_criteria": [
            f"Background-shell probe '{action_id}' wrote its summary to "
            f"{summary_hint}.",
            "Daemon request cancellation and engine background status "
            "produced consistent results for every launch.",
        ],
        "tasks": [
            {"id": action_id, "agent_name": "executor", "deps": []},
        ],
        "task_specs": {action_id: action_spec},
    }


class _BackgroundShellScenarioBase(ScenarioBase):
    """Shared planner/executor/evaluator shape across the 7 scenarios."""

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
    summary_path_hint: str = ""

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_plan_closes_goal,
            _plan(self.action_id, self.action_spec, self.summary_path_hint),
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
    """T1: N concurrent background launches reach ``finished`` cleanly."""

    name = "sandbox.background_shell_golden"
    action_id = "background_shell_golden"
    action_spec = (
        "ACTION background_shell_golden. Launch 3 concurrent background "
        "shells (each sleeps 5 s, echoes 'done'); wait for natural exit; "
        "write the per-launch summary."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/background_shell/golden/summary.json"
    )


class BackgroundShellStop(_BackgroundShellScenarioBase):
    """T2: launch background shells; cancel mid-flight; no leftover state."""

    name = "sandbox.background_shell_stop"
    action_id = "background_shell_stop"
    action_spec = (
        "ACTION background_shell_stop. Launch 3 long-running background "
        "shells, cancel each via asyncio.wait_for after 1 s, then issue a "
        "follow-up foreground shell to confirm post-cancel mount latency "
        "stays under budget."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/background_shell/stop/summary.json"
    )


class BackgroundShellInterleave(_BackgroundShellScenarioBase):
    """T3: 1 long background + M foreground shells, record fg p95 mount."""

    name = "sandbox.background_shell_interleave"
    action_id = "background_shell_interleave"
    action_spec = (
        "ACTION background_shell_interleave. Launch 1 long-running "
        "background shell (sleep 30 s) and 5 foreground shells interleaved; "
        "record per-foreground mount-latency timings."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/background_shell/interleave/summary.json"
    )


class BackgroundShellExhaustion(_BackgroundShellScenarioBase):
    """T5: 80 launches cancelled in unison; AC-14 post-exhaustion read budget."""

    name = "sandbox.background_shell_exhaustion"
    action_id = "background_shell_exhaustion"
    action_spec = (
        "ACTION background_shell_exhaustion. Fire 80 background shell "
        "launches in parallel, each cancelled after 2 s; issue a follow-up "
        "foreground read_file to validate the daemon RPC dispatcher is not "
        "blocked by the shell executor (Pre-mortem #3 / AC-14)."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/background_shell/exhaustion/summary.json"
    )


class BackgroundShellPartialWriteCancel(_BackgroundShellScenarioBase):
    """T6: cancel a long ``dd`` mid-write; assert no leaked OCC publish."""

    name = "sandbox.background_shell_partial_write_cancel"
    action_id = "background_shell_partial_write_cancel"
    action_spec = (
        "ACTION background_shell_partial_write_cancel. Run an 800 MB dd "
        "into a tracked path as a background shell, cancel at 2 s, then "
        "read the target back to confirm the upperdir was discarded."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/background_shell/partial_write/summary.json"
    )


class BackgroundShellStopDuringMaintenance(_BackgroundShellScenarioBase):
    """T7: short shell + maintenance; verify OCC consistency afterwards."""

    name = "sandbox.background_shell_stop_during_maintenance"
    action_id = "background_shell_stop_during_maintenance"
    action_spec = (
        "ACTION background_shell_stop_during_maintenance. Run a short "
        "background shell that writes one file and then sleeps; confirm "
        "the publish + maintenance sequence leaves a consistent OCC state."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/background_shell/maintenance/summary.json"
    )


class BackgroundShellLateCancelRace(_BackgroundShellScenarioBase):
    """T8: await full completion; late cancel must not mutate the result."""

    name = "sandbox.background_shell_late_cancel_race"
    action_id = "background_shell_late_cancel_race"
    action_spec = (
        "ACTION background_shell_late_cancel_race. Await a short background "
        "shell to completion (1 s sleep + echo); assert exit_code 0 and "
        "stdout preserved."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/background_shell/late_cancel/summary.json"
    )


__all__ = [
    "BackgroundShellStop",
    "BackgroundShellStopDuringMaintenance",
    "BackgroundShellExhaustion",
    "BackgroundShellGolden",
    "BackgroundShellInterleave",
    "BackgroundShellLateCancelRace",
    "BackgroundShellPartialWriteCancel",
]
