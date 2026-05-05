"""Composition helper for attempt orchestrator factories."""

from __future__ import annotations

from collections.abc import Callable

from task_center.attempt.state import Attempt
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.runtime import AttemptRuntime


def make_attempt_orchestrator_factory(
    *,
    runtime: AttemptRuntime,
) -> Callable[[Attempt, Callable[[str], None]], AttemptOrchestrator]:
    def factory(
        attempt: Attempt,
        on_attempt_closed: Callable[[str], None],
    ) -> AttemptOrchestrator:
        orchestrator = AttemptOrchestrator(
            attempt=attempt,
            on_attempt_closed=on_attempt_closed,
            runtime=runtime,
        )
        return orchestrator

    return factory
