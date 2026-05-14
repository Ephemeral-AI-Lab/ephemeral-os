"""Shared timing helpers for sandbox operations.

Sandbox results expose one flat ``timings`` mapping. Keep the clock,
elapsed-time writes, payload normalization, and audit-facing key-family rules
here so API wrappers, daemon handlers, OCC, overlay, and layer-stack code do
not each grow their own timing conventions.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, MutableMapping
from enum import Enum
from typing import Literal

from sandbox.timing_keys import TimingKey

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
    return {_timing_key_text(key): float(value) for key, value in raw.items()}


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
    return any(_matches_timing_prefix(key, prefix) for key in timings)


def _has_any_timing(timings: Mapping[object, object], prefixes: tuple[str, ...]) -> bool:
    return any(_has_timing(timings, prefix) for prefix in prefixes)


def _has_auto_squash_fact(
    timings: Mapping[object, object],
    payload: Mapping[str, object],
) -> bool:
    if any("auto_squash" in _timing_key_text(key).lower() for key in timings):
        return True
    return any("auto_squash" in str(key).lower() for key in payload)


def _timing_key_text(key: object) -> str:
    if isinstance(key, Enum):
        return str(key.value)
    text = str(key)
    if text.startswith("TimingKey."):
        return _TIMING_KEY_NAME_TO_VALUE.get(text.removeprefix("TimingKey."), text)
    return text


def _matches_timing_prefix(key: object, prefix: str) -> bool:
    text = _timing_key_text(key)
    if text.startswith(prefix):
        return True
    if not text.startswith("TimingKey."):
        return False
    name = text.removeprefix("TimingKey.").lower()
    return _STRINGIFIED_TIMING_KEY_PREFIXES.get(prefix, ()) and name.startswith(
        _STRINGIFIED_TIMING_KEY_PREFIXES[prefix]
    )


_STRINGIFIED_TIMING_KEY_PREFIXES = {
    "occ.prepare.": ("prepare_",),
    "occ.commit.": ("commit_",),
    "occ.apply.": ("apply_",),
    "occ.direct.": ("direct_",),
    "occ.gated.": ("gated_",),
    "occ.serial.": ("serial_",),
    "layer_stack.lease_": ("layer_transaction_lock_",),
    "layer_stack.transaction_lock_wait": ("layer_transaction_lock_wait",),
    "layer_stack.transaction_lock_held": ("layer_transaction_lock_held",),
    "layer_stack.publish": ("commit_publish_layer",),
}

_TIMING_KEY_NAME_TO_VALUE = {key.name: str(key.value) for key in TimingKey}


__all__ = [
    "TimingAuditSignal",
    "monotonic_now",
    "normalize_timing_map",
    "record_elapsed",
    "timing_audit_signals",
]
