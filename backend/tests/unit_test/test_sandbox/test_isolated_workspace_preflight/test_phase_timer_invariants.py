"""``_PhaseTimer`` unit-level guard for the SUBSET-COVER contract.

PR 1 ships ``_PhaseTimer`` and instruments four manager methods. The fence
here pins three properties that every Tier 9 test will assume:

- ``measure(name)`` records the phase only when its body exits normally.
  A raised exception leaves the key absent (P5: absence != zero).
- ``total_ms()`` reflects the timer's full lifetime, not the sum of phases.
- ``phases_ms`` is a defensive copy (mutating the returned dict does not
  leak into the timer's internal state).

These are pure-Python invariants — they don't need Docker or the daemon.
"""

from __future__ import annotations

import pytest

from sandbox.isolated_workspace._control_plane.types import (
    _PHASE_TIMER_OVERHEAD_BUDGET_MS,
    _PhaseTimer,
)


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_completed_phases_are_recorded() -> None:
    clock = _FakeClock()
    timer = _PhaseTimer(clock)
    clock.t = 0.002
    with timer.measure("a"):
        clock.t = 0.005  # 3 ms
    clock.t = 0.008
    with timer.measure("b"):
        clock.t = 0.010  # 2 ms
    clock.t = 0.012
    assert timer.phases_ms == {"a": 3.0, "b": 2.0}
    assert timer.total_ms() == pytest.approx(12.0)


def test_phase_that_raised_is_absent() -> None:
    clock = _FakeClock()
    timer = _PhaseTimer(clock)
    clock.t = 0.001
    with pytest.raises(RuntimeError):
        with timer.measure("a"):
            clock.t = 0.003
            raise RuntimeError("boom")
    clock.t = 0.005
    with timer.measure("b"):
        clock.t = 0.006
    assert "a" not in timer.phases_ms, "P5: absent != zero"
    assert timer.phases_ms == {"b": 1.0}


def test_subset_cover_holds_for_completed_phases() -> None:
    """``sum(phases_ms) <= total_ms + epsilon`` for every measurement."""
    clock = _FakeClock()
    timer = _PhaseTimer(clock)
    clock.t = 0.001
    with timer.measure("a"):
        clock.t = 0.002
    clock.t = 0.004  # gap between phases
    with timer.measure("b"):
        clock.t = 0.006
    clock.t = 0.010  # trailing time after last phase
    total = timer.total_ms()
    phases_sum = sum(timer.phases_ms.values())
    epsilon = max(_PHASE_TIMER_OVERHEAD_BUDGET_MS, 0.05 * total)
    assert phases_sum <= total + epsilon


def test_phases_ms_returns_defensive_copy() -> None:
    clock = _FakeClock()
    timer = _PhaseTimer(clock)
    with timer.measure("a"):
        clock.t = 0.002
    snapshot = timer.phases_ms
    snapshot["a"] = -999.0
    assert timer.phases_ms == {"a": 2.0}, (
        "phases_ms must return a copy so callers cannot corrupt internal state"
    )


def test_total_ms_independent_of_phase_sum() -> None:
    """Untimed gaps must inflate total_ms past sum(phases_ms)."""
    clock = _FakeClock()
    timer = _PhaseTimer(clock)
    with timer.measure("a"):
        clock.t = 0.001
    # 10 ms untimed gap
    clock.t = 0.011
    with timer.measure("b"):
        clock.t = 0.012
    assert timer.total_ms() == pytest.approx(12.0)
    assert sum(timer.phases_ms.values()) == pytest.approx(2.0)


def test_no_phases_means_empty_dict() -> None:
    """A timer that measured nothing still returns ``{}`` and a positive total."""
    clock = _FakeClock()
    timer = _PhaseTimer(clock)
    clock.t = 0.005
    assert timer.phases_ms == {}
    assert timer.total_ms() == pytest.approx(5.0)
