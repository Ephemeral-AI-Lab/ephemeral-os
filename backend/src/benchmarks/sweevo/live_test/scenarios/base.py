"""Scenario protocol + ScenarioContext + ToolCallSpec + CompositeScenario.

Per plan §10. Scenarios are pure descriptions; the squad runner translates
them into actual tool calls. The protocol is intentionally narrow — the four
decision methods plus a ``hooks()`` declaration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from benchmarks.sweevo.live_test.audit.events import EventType
from benchmarks.sweevo.live_test.hooks.registry import Hook


@dataclass(frozen=True, slots=True)
class ToolCallSpec:
    """Description of an agent submission tool call."""

    tool: Any  # BaseTool
    args: dict[str, Any]


@dataclass(slots=True)
class ScenarioContext:
    """Live state visible to a scenario at a decision point."""

    attempt: Any  # Attempt | None
    episode: Any  # Episode | None
    mission: Any  # Mission | None
    prompt: str
    metadata: Any  # ExecutionMetadata
    audit_recorder: Any  # AuditRecorder | None
    mutable_state: Any  # MutableMockState | None


@runtime_checkable
class Scenario(Protocol):
    """A scenario that drives one mock-agent run end-to-end."""

    name: str
    expected_event_sequence: tuple[EventType, ...]

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec: ...

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[Any]: ...

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec: ...

    def hooks(self) -> Sequence[Hook]: ...


@dataclass(frozen=True, slots=True)
class CompositeScenario:
    """Trivial composition placeholder — phase-1 ships only one scenario.

    The fields and ``compose`` classmethod are exposed so next-phase code can
    bolt actual composition logic onto this surface without breaking imports.
    """

    parts: tuple[Scenario, ...]


class ScenarioBase:
    """Default implementation of the Scenario protocol.

    Subclasses override the four decision methods. ``hooks()`` defaults to no
    hooks. ``compose`` returns a :class:`CompositeScenario` carrying the parts.
    """

    name: str = ""
    expected_event_sequence: tuple[EventType, ...] = ()

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        raise NotImplementedError

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[Any]:  # noqa: ARG002
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        raise NotImplementedError

    def hooks(self) -> Sequence[Hook]:
        return ()

    @classmethod
    def compose(cls, *parts: Scenario) -> CompositeScenario:
        return CompositeScenario(parts=tuple(parts))


__all__ = [
    "CompositeScenario",
    "Scenario",
    "ScenarioBase",
    "ScenarioContext",
    "ToolCallSpec",
]
