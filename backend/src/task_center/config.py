"""Runtime configuration for the harness lifecycle."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HarnessLifecycleConfig:
    """Configurable knobs for the request/segment/graph lifecycle.

    ``default_attempt_budget`` is applied to every TaskSegment created by
    ``ComplexTaskRequestHandler`` unless overridden per-call.
    """

    default_attempt_budget: int = 2
