"""Best-effort rollback saga for lifecycle compensation.

The TaskCenter lifecycle has four overlapping compensation routines that
all follow the same shape: a sequence of best-effort rollback actions,
each wrapped in ``try/except`` so an earlier failure does not block a
later cleanup step. Without a shared abstraction this pattern was
repeated as nested try/except blocks across four files (mission start,
entry startup, retry start, planner launch).

:class:`Saga` is the shared abstraction. Each step is a named callable;
the saga executes them in order and logs (but does not raise) failures
inside the sequence. After the run, callers can inspect
:attr:`Saga.failures` to escalate the most critical failure or trigger a
fallback recovery.

Usage::

    saga = Saga("mission_start_compensation")
    saga.step("close_attempt", lambda: ...)
    saga.step("cancel_episode", lambda: ...)
    saga.step("cancel_mission", lambda: ...)
    saga.step("restore_parent", lambda: ...)
    result = saga.run()
    if result.failures:
        ...  # escalate to fallback recovery
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SagaStep:
    """One named rollback action."""

    name: str
    action: Callable[[], None]


@dataclass(frozen=True, slots=True)
class SagaResult:
    """Outcome of one :meth:`Saga.run` call."""

    saga_name: str
    failures: tuple[tuple[str, BaseException], ...] = ()

    @property
    def all_succeeded(self) -> bool:
        return len(self.failures) == 0


@dataclass
class Saga:
    """Sequenced best-effort rollback executor.

    The saga records the requested steps and runs them in order on
    :meth:`run`. Each step's exception is caught, logged with the saga
    name + step name, and accumulated into the returned
    :class:`SagaResult` so the caller can escalate if needed.

    Sagas are single-shot: once :meth:`run` has been called, attempting
    to mutate the steps or run again raises ``RuntimeError`` so callers
    can't accidentally re-execute compensation.
    """

    name: str
    _steps: list[SagaStep] = field(default_factory=list)
    _done: bool = False

    def step(
        self, name: str, action: Callable[[], None]
    ) -> Saga:
        """Append a named rollback step. Returns ``self`` for chaining."""
        if self._done:
            raise RuntimeError(
                f"Saga {self.name!r} has already run; cannot add step "
                f"{name!r}"
            )
        self._steps.append(SagaStep(name=name, action=action))
        return self

    def run(self) -> SagaResult:
        if self._done:
            raise RuntimeError(
                f"Saga {self.name!r} has already run."
            )
        self._done = True
        failures: list[tuple[str, BaseException]] = []
        for step in self._steps:
            try:
                step.action()
            except Exception as exc:
                logger.exception(
                    "Saga %r: step %r failed", self.name, step.name
                )
                failures.append((step.name, exc))
        return SagaResult(saga_name=self.name, failures=tuple(failures))


__all__ = ["Saga", "SagaResult", "SagaStep"]
