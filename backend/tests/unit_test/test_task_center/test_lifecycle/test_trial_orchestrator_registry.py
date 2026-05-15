"""TrialOrchestratorRegistry tests."""

from __future__ import annotations

import pytest

from task_center._core.types import TaskCenterInvariantViolation
from task_center.trial.orchestrator_registry import (
    TrialOrchestratorRegistry,
)


class _FakeOrchestrator:
    def __init__(self, attempt_id: str) -> None:
        self.trial_id = attempt_id


def test_registry_enforces_one_orchestrator_per_graph():
    registry = TrialOrchestratorRegistry()
    registry.register(_FakeOrchestrator("g1"))  # type: ignore[arg-type]

    with pytest.raises(TaskCenterInvariantViolation):
        registry.register(_FakeOrchestrator("g1"))  # type: ignore[arg-type]


def test_registry_deregister_allows_replacement():
    registry = TrialOrchestratorRegistry()
    first = _FakeOrchestrator("g1")
    second = _FakeOrchestrator("g1")

    registry.register(first)  # type: ignore[arg-type]
    registry.deregister("g1")
    registry.register(second)  # type: ignore[arg-type]

    assert registry.get("g1") is second
