"""``with_thresholds`` context manager for layer-stack live tests.

Plan §3.4 requires that any test mutating layer-stack thresholds restore
defaults in ``finally``; this helper makes that unmissable. Each call
yields a fresh :class:`LayerStackManager` whose :class:`LeaseBudgetWorker`
is configured with the requested caps. Squash trigger is a per-call
argument to :py:meth:`LayerStackManager.squash`, so the chosen value is
returned on the :class:`ConfiguredManager` for the test to pass through.

Plan-name → constructor-name mapping:

| Plan name                    | Backed by                                  |
|------------------------------|--------------------------------------------|
| ``MAX_DEPTH``                | ``manager.squash(max_depth=...)``          |
| ``SQUASH_TRIGGER``           | alias of ``MAX_DEPTH``                     |
| ``EMERGENCY_DEPTH``          | ``LeaseBudgetWorker.max_active_depth``     |
| ``MAX_LEASE_AGE``            | ``LeaseBudgetWorker.kill_lease_age_seconds`` |
| ``WARN_LEASE_AGE``           | ``LeaseBudgetWorker.warn_lease_age_seconds`` |
| ``PER_SESSION_PIN_BYTES``    | ``LeaseBudgetWorker.evict_session_pinned_bytes`` |
| ``GLOBAL_PIN_BYTES``         | ``LeaseBudgetWorker.max_pinned_bytes``     |
| ``MAX_PINNED_OLD_MANIFESTS`` | not yet in code (rejected if non-None)     |
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sandbox.layer_stack.lease_budget import LeaseBudgetWorker
from sandbox.layer_stack.stack_manager import LayerStackManager


@dataclass(frozen=True)
class ConfiguredManager:
    manager: LayerStackManager
    max_depth: int


_DEFAULT_MAX_DEPTH = 90
_UNSET: object = object()


@contextmanager
def with_thresholds(
    storage_root: str | Path,
    *,
    MAX_DEPTH: int = _DEFAULT_MAX_DEPTH,
    SQUASH_TRIGGER: int | object = _UNSET,
    EMERGENCY_DEPTH: int | None = None,
    MAX_LEASE_AGE: float | None = None,
    WARN_LEASE_AGE: float | None = None,
    PER_SESSION_PIN_BYTES: int | None = None,
    GLOBAL_PIN_BYTES: int | None = None,
    MAX_PINNED_OLD_MANIFESTS: int | None = None,
) -> Iterator[ConfiguredManager]:
    if SQUASH_TRIGGER is not _UNSET and SQUASH_TRIGGER != MAX_DEPTH:
        raise ValueError(
            "SQUASH_TRIGGER currently aliases MAX_DEPTH; pass only one or "
            "matching values"
        )
    if MAX_PINNED_OLD_MANIFESTS is not None:
        raise NotImplementedError(
            "MAX_PINNED_OLD_MANIFESTS has no backing in LeaseBudgetWorker"
        )

    budget = LeaseBudgetWorker(
        max_active_depth=EMERGENCY_DEPTH,
        max_pinned_bytes=GLOBAL_PIN_BYTES,
        warn_lease_age_seconds=WARN_LEASE_AGE,
        kill_lease_age_seconds=MAX_LEASE_AGE,
        evict_session_pinned_bytes=PER_SESSION_PIN_BYTES,
    )
    manager = LayerStackManager(Path(storage_root), lease_budget=budget)
    yield ConfiguredManager(manager=manager, max_depth=MAX_DEPTH)


__all__ = ["ConfiguredManager", "with_thresholds"]
