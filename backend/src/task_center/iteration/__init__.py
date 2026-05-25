"""Iteration package facade.

Iteration DTOs/enums live in :mod:`task_center.iteration.state`; lifecycle
coordination lives in :mod:`task_center.iteration.attempt_coordinator`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_center.iteration.attempt_coordinator import (
        AttemptClosedCallback as AttemptClosedCallback,
        IterationAttemptCoordinator as IterationAttemptCoordinator,
        IterationClosureSink as IterationClosureSink,
        OpenIterationCoordinatorRegistry as OpenIterationCoordinatorRegistry,
        OrchestratorFactory as OrchestratorFactory,
    )
    from task_center.iteration.state import (
        AttemptPlanFailed as AttemptPlanFailed,
        ClosureOutcome as ClosureOutcome,
        Iteration as Iteration,
        IterationClosureReport as IterationClosureReport,
        IterationCreationReason as IterationCreationReason,
        IterationStatus as IterationStatus,
        PriorAttemptEntry as PriorAttemptEntry,
        SuccessDeferred as SuccessDeferred,
        TerminalSuccess as TerminalSuccess,
    )

_COORDINATORS = "task_center.iteration.attempt_coordinator"
_STATE = "task_center.iteration.state"

_EXPORTS: dict[str, tuple[str, str]] = {
    "AttemptClosedCallback": (
        _COORDINATORS,
        "AttemptClosedCallback",
    ),
    "AttemptPlanFailed": (_STATE, "AttemptPlanFailed"),
    "ClosureOutcome": (_STATE, "ClosureOutcome"),
    "Iteration": (_STATE, "Iteration"),
    "IterationClosureSink": (
        _COORDINATORS,
        "IterationClosureSink",
    ),
    "IterationClosureReport": (_STATE, "IterationClosureReport"),
    "IterationCreationReason": (_STATE, "IterationCreationReason"),
    "IterationAttemptCoordinator": (
        _COORDINATORS,
        "IterationAttemptCoordinator",
    ),
    "IterationStatus": (_STATE, "IterationStatus"),
    "OpenIterationCoordinatorRegistry": (
        _COORDINATORS,
        "OpenIterationCoordinatorRegistry",
    ),
    "OrchestratorFactory": (
        _COORDINATORS,
        "OrchestratorFactory",
    ),
    "PriorAttemptEntry": (_STATE, "PriorAttemptEntry"),
    "SuccessDeferred": (_STATE, "SuccessDeferred"),
    "TerminalSuccess": (_STATE, "TerminalSuccess"),
}


def __getattr__(name: str) -> object:
    try:
        module_path, attr = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'task_center.iteration' has no attribute {name!r}") from exc
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


__all__ = sorted(_EXPORTS)
