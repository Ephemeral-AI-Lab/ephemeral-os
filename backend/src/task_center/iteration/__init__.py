"""Iteration package facade.

Iteration DTOs/enums live in :mod:`task_center.iteration.state`; lifecycle
coordination lives in :mod:`task_center.iteration.manager`.
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
    from task_center.iteration.manager import (
        AttemptClosedCallback,
        ClosureReportSink,
        IterationManager,
        IterationManagerRegistry,
        OrchestratorFactory,
    )

_MANAGER_EXPORTS: dict[str, tuple[str, str]] = {
    "AttemptClosedCallback": (
        "task_center.iteration.manager",
        "AttemptClosedCallback",
    ),
    "ClosureReportSink": (
        "task_center.iteration.manager",
        "ClosureReportSink",
    ),
    "IterationManager": ("task_center.iteration.manager", "IterationManager"),
    "IterationManagerRegistry": (
        "task_center.iteration.manager",
        "IterationManagerRegistry",
    ),
    "OrchestratorFactory": (
        "task_center.iteration.manager",
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
    target = _MANAGER_EXPORTS.get(name)
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
    "ClosureReportSink",
    "Iteration",
    "IterationClosureReport",
    "IterationCreationReason",
    "IterationManager",
    "IterationManagerRegistry",
    "IterationStatus",
    "OrchestratorFactory",
    "SuccessDeferred",
    "TerminalSuccess",
]
