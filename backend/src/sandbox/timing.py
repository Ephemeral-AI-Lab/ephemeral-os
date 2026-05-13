"""Shared timing helpers for sandbox operations.

Sandbox results expose one flat ``timings`` mapping. Keep the clock,
elapsed-time writes, payload normalization, and audit-facing key-family rules
here so API wrappers, daemon handlers, OCC, overlay, and layer-stack code do
not each grow their own timing conventions.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, MutableMapping
from typing import Literal

TimingAuditSignal = Literal[
    "occ_prepared",
    "occ_committed",
    "occ_conflicted",
    "overlay_executed",
    "layer_stack_lease_acquired",
    "layer_stack_layer_published",
    "layer_stack_auto_squashed",
]


def monotonic_now() -> float:
    """Return the sandbox timing clock."""
    return time.perf_counter()


def record_elapsed(
    timings: MutableMapping[str, float] | None,
    key: str,
    started_at: float,
) -> float:
    """Record and return elapsed seconds for ``key`` when a timing map exists."""
    elapsed = monotonic_now() - started_at
    if timings is not None:
        timings[key] = elapsed
    return elapsed


def normalize_timing_map(raw: Mapping[object, object] | None) -> dict[str, float]:
    """Project arbitrary timing payloads into ``dict[str, float]``."""
    if not raw:
        return {}
    return {str(key): float(value) for key, value in raw.items()}


def timing_audit_signals(
    timings: Mapping[object, object],
    *,
    status: object,
    payload: Mapping[str, object] | None = None,
) -> tuple[TimingAuditSignal, ...]:
    """Return audit signal names implied by sandbox timing keys."""
    if not timings:
        return ()

    emitted: list[TimingAuditSignal] = []
    if _has_timing(timings, "occ.prepare."):
        emitted.append("occ_prepared")
    if _has_timing(timings, "occ.") and status == "conflict":
        emitted.append("occ_conflicted")
    elif _has_any_timing(timings, ("occ.commit.", "occ.apply.")) and status == "ok":
        emitted.append("occ_committed")

    if _has_any_timing(timings, ("overlay.", "command_exec.")):
        emitted.append("overlay_executed")

    if _has_any_timing(
        timings,
        (
            "layer_stack.lease_",
            "layer_stack.transaction_lock_wait",
            "layer_stack.transaction_lock_held",
        ),
    ):
        emitted.append("layer_stack_lease_acquired")
    if _has_any_timing(timings, ("layer_stack.publish", "layer_stack.layer_")):
        emitted.append("layer_stack_layer_published")
    if _has_auto_squash_fact(timings, payload or {}):
        emitted.append("layer_stack_auto_squashed")
    return tuple(emitted)


def _has_timing(timings: Mapping[object, object], prefix: str) -> bool:
    return any(str(key).startswith(prefix) for key in timings)


def _has_any_timing(timings: Mapping[object, object], prefixes: tuple[str, ...]) -> bool:
    return any(_has_timing(timings, prefix) for prefix in prefixes)


def _has_auto_squash_fact(
    timings: Mapping[object, object],
    payload: Mapping[str, object],
) -> bool:
    if any("auto_squash" in str(key) for key in timings):
        return True
    return any("auto_squash" in str(key) for key in payload)


__all__ = [
    "TimingAuditSignal",
    "monotonic_now",
    "normalize_timing_map",
    "record_elapsed",
    "timing_audit_signals",
]
