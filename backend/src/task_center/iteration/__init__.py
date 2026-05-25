"""Iteration package facade.

Iteration DTOs/enums live in :mod:`task_center.iteration.state`; lifecycle
coordination lives in :mod:`task_center.iteration.attempt_coordinator`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.iteration.state import (
    PriorAttemptEntry,
    AttemptPlanFailed,
    ClosureOutcome,
    Iteration,
    IterationClosureReport,
    IterationCreationReason,
    IterationStatus,
    SuccessDeferred,
    TerminalSuccess,
)

if TYPE_CHECKING:
    from task_center.iteration.attempt_coordinator import (
        AttemptClosedCallback,
        IterationClosureSink,
        IterationAttemptCoordinator,
        OpenIterationCoordinatorRegistry,
        OrchestratorFactory,
    )

_COORDINATOR_EXPORTS: dict[str, tuple[str, str]] = {
    "AttemptClosedCallback": (
        "task_center.iteration.attempt_coordinator",
        "AttemptClosedCallback",
    ),
    "IterationClosureSink": (
        "task_center.iteration.attempt_coordinator",
        "IterationClosureSink",
    ),
    "IterationAttemptCoordinator": (
        "task_center.iteration.attempt_coordinator",
        "IterationAttemptCoordinator",
    ),
    "OpenIterationCoordinatorRegistry": (
        "task_center.iteration.attempt_coordinator",
        "OpenIterationCoordinatorRegistry",
    ),
    "OrchestratorFactory": (
        "task_center.iteration.attempt_coordinator",
        "OrchestratorFactory",
    ),
}

_STATE_EXPORTS = [
    "AttemptPlanFailed",
    "PriorAttemptEntry",
    "ClosureOutcome",
    "Iteration",
    "IterationClosureReport",
    "IterationCreationReason",
    "IterationStatus",
    "SuccessDeferred",
    "TerminalSuccess",
]


def __getattr__(name: str) -> object:
    target = _COORDINATOR_EXPORTS.get(name)
    if target is None:
        raise AttributeError(
            f"module 'task_center.iteration' has no attribute {name!r}"
        )
    module_path, attr = target
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


__all__ = [
    "AttemptClosedCallback",
    "AttemptPlanFailed",
    "PriorAttemptEntry",
    "ClosureOutcome",
    "IterationClosureSink",
    "Iteration",
    "IterationClosureReport",
    "IterationCreationReason",
    "IterationAttemptCoordinator",
    "OpenIterationCoordinatorRegistry",
    "IterationStatus",
    "OrchestratorFactory",
    "SuccessDeferred",
    "TerminalSuccess",
]
