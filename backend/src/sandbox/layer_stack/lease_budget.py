"""Lease-pressure decisions for sandbox layer stacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


BudgetDecisionKind = Literal[
    "allow",
    "backpressure_commits",
]


@dataclass(frozen=True)
class BudgetDecision:
    kind: BudgetDecisionKind
    reason: str


class LeaseBudgetWorker:
    """Evaluates publish backpressure from active-depth and pinned-byte pressure."""

    def __init__(
        self,
        *,
        max_active_depth: int | None = None,
        max_pinned_bytes: int | None = None,
    ) -> None:
        _validate_non_negative_int("max_active_depth", max_active_depth)
        _validate_non_negative_int("max_pinned_bytes", max_pinned_bytes)
        self._max_active_depth = max_active_depth
        self._max_pinned_bytes = max_pinned_bytes

    def evaluate(
        self,
        *,
        active_depth: int,
        pinned_bytes: int = 0,
    ) -> BudgetDecision:
        if active_depth < 0:
            raise ValueError("active_depth must be non-negative")
        if pinned_bytes < 0:
            raise ValueError("pinned_bytes must be non-negative")

        if self._max_active_depth is not None and active_depth >= self._max_active_depth:
            return BudgetDecision(
                kind="backpressure_commits",
                reason=(
                    f"active manifest depth {active_depth} reached limit {self._max_active_depth}"
                ),
            )

        if self._max_pinned_bytes is not None and pinned_bytes >= self._max_pinned_bytes:
            return BudgetDecision(
                kind="backpressure_commits",
                reason=(
                    f"snapshot leases pin {pinned_bytes} bytes, limit {self._max_pinned_bytes}"
                ),
            )

        return BudgetDecision(kind="allow", reason="lease budget allows commits")


def _validate_non_negative_int(name: str, value: int | None) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{name} must be non-negative")
