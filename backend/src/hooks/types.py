"""Runtime hook result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HookResult:
    """Result from a single hook execution."""

    hook_type: str
    success: bool
    output: str = ""
    blocked: bool = False
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AggregatedHookResult:
    """Aggregated result for a hook event."""

    results: list[HookResult] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """Return whether any hook blocked continuation."""
        return any(result.blocked for result in self.results)

    @property
    def reason(self) -> str:
        """Return the first blocking reason, if any."""
        for result in self.results:
            if result.blocked:
                return result.reason or result.output
        return ""
