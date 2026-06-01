"""``ScenarioLifecycle`` — captures mock side-channel audit events.

Mock scenarios run through the same ``run_pipeline`` as real-agent and
benchmark runs. This lifecycle is the mock-specific seam: ``on_event`` is
subscribed to the audit bus at engine startup and accumulates the captured
events plus typed ``MOCK_*`` records for ``RunReport``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from test_runner.audit.events import EventType
from test_runner.agent.mock.prompt_inspector import (
    LaunchRecord,
    PromptInspection,
    ToolCallRecord,
)
from test_runner.agent.mock.sandbox_probe import SandboxCheck

if TYPE_CHECKING:
    from test_runner.audit.events import Event
    from test_runner.core.config import RunContext
    from test_runner.core.report import PipelineReport


class ScenarioLifecycle:
    """``LifecycleHooks`` implementation for the mock-scenario mode."""

    def __init__(self) -> None:
        self._captured_events: list[Event] = []
        self._launches: list[LaunchRecord] = []
        self._tool_calls: list[ToolCallRecord] = []
        self._prompt_inspections: list[PromptInspection] = []
        self._sandbox_checks: list[SandboxCheck] = []

    @property
    def captured_events(self) -> list["Event"]:
        return self._captured_events

    @property
    def launches(self) -> list[LaunchRecord]:
        return self._launches

    @property
    def tool_calls(self) -> list[ToolCallRecord]:
        return self._tool_calls

    @property
    def prompt_inspections(self) -> list[PromptInspection]:
        return self._prompt_inspections

    @property
    def sandbox_checks(self) -> list[SandboxCheck]:
        return self._sandbox_checks

    async def before_run(self, _ctx: "RunContext") -> None:
        return None

    def on_event(self, event: "Event") -> None:
        self._captured_events.append(event)
        if event.type == EventType.MOCK_LAUNCH_RECORDED:
            self._launches.append(LaunchRecord(**event.payload))
        elif event.type == EventType.MOCK_TOOL_CALL_RECORDED:
            self._tool_calls.append(ToolCallRecord(**event.payload))
        elif event.type == EventType.MOCK_PROMPT_INSPECTED:
            self._prompt_inspections.append(PromptInspection(**event.payload))
        elif event.type == EventType.MOCK_SANDBOX_CHECK_RECORDED:
            payload = dict(event.payload)
            # ``SandboxCheck.changed_paths`` is a tuple; ``dataclasses.asdict``
            # used at the publish site keeps tuples as tuples but defensive
            # callers (or future bus serialization) might emit a list.
            cp = payload.get("changed_paths", ())
            payload["changed_paths"] = tuple(cp) if not isinstance(cp, tuple) else cp
            self._sandbox_checks.append(SandboxCheck(**payload))

    async def after_run(self, _ctx: "RunContext", _report: "PipelineReport") -> None:
        return None

    async def on_aborted(self, _ctx: "RunContext", _reason: str) -> None:
        return None


__all__ = ["ScenarioLifecycle"]
