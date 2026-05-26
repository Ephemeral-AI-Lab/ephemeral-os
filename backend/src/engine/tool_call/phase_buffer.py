"""Per-call phase buffer + per-tool rolling P95 for slow-tail flush.

Phase 2.6 slice 7. See V3 README §Per-tool phase sampling rule for the
mechanism. Two pieces of state live here:

1. **Per-call phase buffer** — a ``contextvars.ContextVar`` carrying a
   fixed-size deque of ``(phase, duration_ms)`` records (max 6 entries —
   one per phase: queued/mount/exec/capture/publish/release). Each
   foreground tool dispatch sets up a fresh buffer; ``record_phase``
   appends into the active buffer.

2. **Per-``tool_name`` rolling window** — last 100 ``total_ms`` values per
   tool name, protected by a per-tool-name ``threading.Lock``. The
   critical section is O(1): append + drop-oldest + P95 lookup via a
   sorted-list helper. An OrderedDict-based LRU caps the dict at 256
   distinct ``tool_name`` strings so a misbehaving agent that invents
   arbitrary tool names cannot grow it without bound.

The dispatcher decides on ``tool_call.finished`` whether to flush:

- **Cold window** — rolling window has < 100 samples → ALWAYS flush.
- **Slow tail** — rolling window full AND ``total_ms ≥ P95`` → flush.
- **Otherwise** — discard the buffer; ``phase_totals_rollup`` is still
  computed from the in-process records in :func:`finish_phase_buffer`.

Zero new threads. Locks are only acquired briefly to update the per-tool
rolling window.
"""

from __future__ import annotations

import contextvars
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Final

PHASE_QUEUED: Final[str] = "queued"
PHASE_MOUNT: Final[str] = "mount"
PHASE_EXEC: Final[str] = "exec"
PHASE_CAPTURE: Final[str] = "capture"
PHASE_PUBLISH: Final[str] = "publish"
PHASE_RELEASE: Final[str] = "release"

_ROLLING_WINDOW_SIZE: Final[int] = 100
_PHASE_BUFFER_MAX: Final[int] = 6
_TOOL_NAME_LRU_CAP: Final[int] = 256


@dataclass
class _PhaseRecord:
    phase: str
    duration_ms: float


@dataclass
class _PhaseBuffer:
    tool_id: str
    tool_name: str
    entries: deque[_PhaseRecord] = field(
        default_factory=lambda: deque(maxlen=_PHASE_BUFFER_MAX)
    )


_active_buffer: contextvars.ContextVar[_PhaseBuffer | None] = contextvars.ContextVar(
    "engine_tool_call_phase_buffer", default=None
)


@dataclass
class _RollingWindow:
    samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=_ROLLING_WINDOW_SIZE)
    )
    lock: threading.Lock = field(default_factory=threading.Lock)

    def append_and_p95(self, total_ms: float) -> tuple[int, float | None]:
        """Append a sample; return (prior_size, p95_before_append).

        ``prior_size`` is the number of samples in the window BEFORE this
        call's ``total_ms`` is appended. ``p95_before_append`` is the P95
        of those prior samples (None when the window was empty).

        Returning p95 *before* the append matches the plan's slow-tail rule:
        compare the just-finished call's ``total_ms`` against the P95 of the
        previously observed calls — without the just-finished call biasing
        its own P95.
        """
        with self.lock:
            prior_size = len(self.samples)
            prior = sorted(self.samples) if prior_size > 0 else []
            self.samples.append(total_ms)
        if not prior:
            return prior_size, None
        # Index-based percentile: P95 of N points → samples[ceil(0.95*N)-1].
        # statistics.quantiles is overkill at N=100.
        idx = max(0, min(len(prior) - 1, int(round(0.95 * len(prior))) - 1))
        return prior_size, prior[idx]


class _RollingWindowRegistry:
    """LRU-capped registry mapping ``tool_name`` -> :class:`_RollingWindow`."""

    def __init__(self, cap: int = _TOOL_NAME_LRU_CAP) -> None:
        self._cap = cap
        self._windows: OrderedDict[str, _RollingWindow] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, tool_name: str) -> _RollingWindow:
        with self._lock:
            window = self._windows.get(tool_name)
            if window is None:
                window = _RollingWindow()
                self._windows[tool_name] = window
                if len(self._windows) > self._cap:
                    self._windows.popitem(last=False)
            else:
                self._windows.move_to_end(tool_name)
            return window

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


_registry = _RollingWindowRegistry()


def reset_for_tests() -> None:
    """Test helper — wipe the per-tool rolling windows."""
    _registry.clear()


def start_phase_buffer(tool_id: str, tool_name: str) -> _PhaseBuffer:
    """Install a fresh phase buffer in the current context.

    Returns the new buffer; the caller is expected to record phases on it
    via :func:`record_phase` and finish via :func:`finish_phase_buffer`.
    """
    buf = _PhaseBuffer(tool_id=tool_id, tool_name=tool_name)
    _active_buffer.set(buf)
    return buf


def record_phase(phase: str, duration_ms: float) -> None:
    """Append a phase record onto the active buffer, if one exists.

    Silently no-ops when called outside a tool dispatch (no active buffer).
    Unknown phase names are still recorded — they will surface in
    ``phase_totals_rollup`` as an extra key. Validation is enforced at the
    schema layer when emitting, not here.
    """
    buf = _active_buffer.get()
    if buf is None:
        return
    buf.entries.append(_PhaseRecord(phase=phase, duration_ms=float(duration_ms)))


@dataclass(frozen=True)
class FinishedPhaseDecision:
    flush: bool
    cold_window: bool
    phases: tuple[_PhaseRecord, ...]
    rollup: dict[str, float]


def finish_phase_buffer(total_ms: float) -> FinishedPhaseDecision:
    """Decide flush vs discard for the active buffer and clear it.

    Returns a :class:`FinishedPhaseDecision` carrying:

    - ``flush`` — True when the dispatcher should emit ``tool_call.phase``
      events for each entry in ``phases``.
    - ``cold_window`` — True when the rolling window had < 100 samples
      before this call's ``total_ms`` was appended.
    - ``phases`` — the phase records captured for this call (regardless of
      ``flush``; the dispatcher may use them for the rollup).
    - ``rollup`` — the ``phase_totals_rollup`` map summed over ``phases``;
      ALWAYS populated, regardless of the flush decision.

    The buffer is cleared on return so subsequent calls reusing the same
    context do not see leftover entries.
    """
    buf = _active_buffer.get()
    entries: tuple[_PhaseRecord, ...] = ()
    rollup: dict[str, float] = {}
    if buf is not None:
        entries = tuple(buf.entries)
        for entry in entries:
            rollup[entry.phase] = rollup.get(entry.phase, 0.0) + entry.duration_ms
    _active_buffer.set(None)

    if buf is None:
        return FinishedPhaseDecision(
            flush=False, cold_window=False, phases=(), rollup=rollup
        )

    window = _registry.get(buf.tool_name)
    prior_size, prior_p95 = window.append_and_p95(total_ms)
    cold_window = prior_size < _ROLLING_WINDOW_SIZE
    slow_tail = prior_p95 is not None and total_ms >= prior_p95
    flush = cold_window or slow_tail
    return FinishedPhaseDecision(
        flush=flush, cold_window=cold_window, phases=entries, rollup=rollup
    )


__all__ = [
    "FinishedPhaseDecision",
    "PHASE_CAPTURE",
    "PHASE_EXEC",
    "PHASE_MOUNT",
    "PHASE_PUBLISH",
    "PHASE_QUEUED",
    "PHASE_RELEASE",
    "finish_phase_buffer",
    "record_phase",
    "reset_for_tests",
    "start_phase_buffer",
]
