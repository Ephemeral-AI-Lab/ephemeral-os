"""``ScenarioLifecycle`` — bridges scenario hooks into the audit-bus pipeline.

Phase 4 of the task_center_runner restructure
(.omc/plans/task_center_runner-restructure.md) makes mock scenarios run
through the same ``run_pipeline`` as real-LLM freeform runs and SWE-EVO
benchmark runs. The only mode-specific seam for mock scenarios is this
lifecycle: ``on_event`` is subscribed to the audit bus at engine startup
and fires the scenario's ``HookSet`` against the shared
``MutableMockState``. ``after_run`` writes the accumulated hook results
into ``PipelineReport.lifecycle_extras["hook_results"]`` so the legacy
``live_e2e/runner.py`` shim can rebuild the rich ``RunReport`` view.

This module is additive in Phase 4b: nothing imports it yet. Phase 4e
(``run_scenario`` shim assembly) wires it through ``build_scenario_config``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center_runner.hooks.registry import HookResult, HookSet, MutableMockState
from task_center_runner.scenarios.base import Scenario

if TYPE_CHECKING:
    from task_center_runner.audit.events import Event
    from task_center_runner.core.config import RunContext
    from task_center_runner.core.report import PipelineReport


class ScenarioLifecycle:
    """``LifecycleHooks`` implementation for the mock-scenario mode."""

    def __init__(
        self,
        *,
        scenario: Scenario,
        hook_set: HookSet,
        mutable_state: MutableMockState,
    ) -> None:
        self._scenario = scenario
        self._hook_set = hook_set
        self._mutable_state = mutable_state
        self._hook_results: list[HookResult] = []
        self._captured_events: list[Event] = []

    @property
    def captured_events(self) -> list["Event"]:
        return self._captured_events

    @property
    def hook_results(self) -> list[HookResult]:
        return self._hook_results

    async def before_run(self, ctx: "RunContext") -> None:
        return None

    def on_event(self, event: "Event") -> None:
        self._captured_events.append(event)
        self._mutable_state.seen_events.append(event.type)
        for result in self._hook_set.fire(event, "post", self._mutable_state):
            self._hook_results.append(result)

    async def after_run(self, ctx: "RunContext", report: "PipelineReport") -> None:
        report.lifecycle_extras.setdefault(
            "hook_results", list(self._hook_results)
        )
        report.lifecycle_extras.setdefault(
            "captured_events", list(self._captured_events)
        )
        report.lifecycle_extras.setdefault(
            "mutable_state_flags", dict(self._mutable_state.flags)
        )
        report.lifecycle_extras.setdefault(
            "seen_event_types", list(self._mutable_state.seen_events)
        )

    async def on_aborted(self, ctx: "RunContext", reason: str) -> None:
        return None


__all__ = ["ScenarioLifecycle"]
