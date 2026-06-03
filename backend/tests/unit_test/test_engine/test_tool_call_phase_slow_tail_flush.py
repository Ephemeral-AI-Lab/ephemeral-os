"""Phase 2.6 slice 7 — slow-tail buffered flush rule.

Drives :func:`engine.tool_call.phase_buffer.finish_phase_buffer` directly
with deterministic ``total_ms`` values and the dispatcher's emit helpers
so we can validate the cold-window + P95 slow-tail decision without
spinning up the full streaming dispatcher.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import engine.tool_call.dispatch as dispatch_mod
from engine.tool_call.dispatch import (
    _emit_tool_call_phase_and_finished,
    _emit_tool_call_started,
)
from engine.tool_call.phase_buffer import (
    PHASE_CAPTURE,
    PHASE_EXEC,
    PHASE_MOUNT,
    PHASE_PUBLISH,
    PHASE_QUEUED,
    PHASE_RELEASE,
    record_phase,
    reset_for_tests,
    start_phase_buffer,
)
from message.message import ToolUseBlock


_EVENTS: list[dict[str, Any]] = []


def _drain_tool_call_events() -> list[dict[str, Any]]:
    events = [
        evt
        for evt in _EVENTS
        if str(evt.get("type", "")).startswith("tool_call.")
    ]
    _EVENTS.clear()
    return events


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_for_tests()
    _EVENTS.clear()

    def _record(event: dict[str, Any], lane: str) -> None:
        _EVENTS.append({**event, "lane": lane})

    monkeypatch.setattr(dispatch_mod, "safe_emit", _record)
    yield
    _EVENTS.clear()


def _simulate_call(tool_name: str, call_index: int, *, total_ms: float) -> None:
    """Run one synthetic dispatch — 6 phases, envelope start + finish."""
    tool_call = ToolUseBlock(
        tool_use_id=f"{tool_name}-{call_index}",
        name=tool_name,
        input={},
    )
    start_phase_buffer(tool_use_id=tool_call.tool_use_id, tool_name=tool_name)
    _emit_tool_call_started(tool_call)
    # Record all 6 phases with deterministic per-phase shares.
    per_phase = total_ms / 6.0
    for phase in (
        PHASE_QUEUED,
        PHASE_MOUNT,
        PHASE_EXEC,
        PHASE_CAPTURE,
        PHASE_PUBLISH,
        PHASE_RELEASE,
    ):
        record_phase(phase, per_phase)
    _emit_tool_call_phase_and_finished(
        tool_call, total_ms=total_ms, exit_status="ok"
    )


def test_tool_call_phase_slow_tail_flush() -> None:
    """200 calls; cold window flushes 100 of 100; slow tail flushes 10 of 100.

    The 90 fast back-half calls use a baseline (1.0 ms) STRICTLY BELOW the
    cold-window timings (10.0 ms) so they never tie with the rolling-window
    P95 — otherwise integer ties at 10 ms would force the ``total_ms ≥ P95``
    gate open for every fast back-half call.
    """
    # Cold window: 100 calls at 10 ms.
    cold = [10.0] * 100
    # Back half: 90 fast at 1 ms (below cold P95), 10 slow at 500 ms.
    back_half = [1.0] * 100
    for i in range(10):
        back_half[i * 10] = 500.0
    timings = cold + back_half
    assert len(timings) == 200

    for i, ms in enumerate(timings):
        _simulate_call("smoke_tool", i, total_ms=ms)

    events = _drain_tool_call_events()

    started = [e for e in events if e["type"] == "tool_call.started"]
    finished = [e for e in events if e["type"] == "tool_call.finished"]
    phase_events = [e for e in events if e["type"] == "tool_call.phase"]
    assert len(started) == 200
    assert len(finished) == 200

    phase_calls: set[str] = set()
    for evt in phase_events:
        phase_calls.add(evt["payload"]["tool_call"]["tool_use_id"])

    # Cold window: 100 calls × 6 phases. Slow tail: exactly 10 back-half slow
    # calls × 6 phases. Fast back-half calls (1.0 ms) sit below P95 and DO
    # NOT flush.
    expected_flushed_calls = 100 + 10
    assert len(phase_calls) == expected_flushed_calls
    assert len(phase_events) == expected_flushed_calls * 6


def test_tool_call_finished_rollup_present_when_phases_discarded() -> None:
    """Warm + fast call still gets phase_totals_rollup on finished envelope.

    Warm-up uses 10 ms calls so the rolling window's P95 is 10 ms; the
    follow-up call at 1 ms sits STRICTLY below P95 so the slow-tail gate
    stays closed and phase events are discarded. The rollup must still
    populate via in-process timers (the contextvar phase records).
    """
    for i in range(100):
        _simulate_call("hot_tool", i, total_ms=10.0)
    _drain_tool_call_events()  # discard warm-up

    _simulate_call("hot_tool", 999, total_ms=1.0)
    events = _drain_tool_call_events()
    phase_events = [e for e in events if e["type"] == "tool_call.phase"]
    finished = [e for e in events if e["type"] == "tool_call.finished"]
    assert phase_events == []  # not slow tail, not cold → no phase events
    assert len(finished) == 1
    rollup = finished[0]["payload"]["tool_call"].get("phase_totals_rollup")
    assert rollup is not None
    assert set(rollup.keys()) == {
        "queued",
        "mount",
        "exec",
        "capture",
        "publish",
        "release",
    }


def test_tool_call_envelope_always_emits_on_normal_lane() -> None:
    """Both started + finished must land on the normal lane unconditionally."""
    _simulate_call("any_tool", 1, total_ms=42.0)
    events = _drain_tool_call_events()
    started = next(e for e in events if e["type"] == "tool_call.started")
    finished = next(e for e in events if e["type"] == "tool_call.finished")
    assert started["lane"] == "normal"
    assert finished["lane"] == "normal"


def test_tool_call_phase_buffer_thread_local_under_many_foreground() -> None:
    """Concurrent ``asyncio.create_task`` calls each see a private phase buffer.

    ``contextvars.ContextVar`` copies into spawned tasks; the slow-tail
    decision must therefore be made per-call without one task's record
    leaking into another's.
    """

    async def _run_one(tool_use_id: str, tool_name: str, phase_ms: float) -> None:
        start_phase_buffer(tool_use_id=tool_use_id, tool_name=tool_name)
        record_phase(PHASE_QUEUED, phase_ms)
        # Yield to the loop so all four tasks interleave their record_phase
        # calls — proves the contextvar isolation, not just sequential calls.
        await asyncio.sleep(0)
        record_phase(PHASE_EXEC, phase_ms)
        tool_call = ToolUseBlock(tool_use_id=tool_use_id, name=tool_name, input={})
        _emit_tool_call_phase_and_finished(
            tool_call, total_ms=phase_ms * 2.0, exit_status="ok"
        )

    async def _drive() -> None:
        tasks = [
            asyncio.create_task(_run_one(f"id-{i}", f"tool-{i}", float(10 + i)))
            for i in range(4)
        ]
        await asyncio.gather(*tasks)

    asyncio.run(_drive())

    events = _drain_tool_call_events()
    finished = [e for e in events if e["type"] == "tool_call.finished"]
    assert len(finished) == 4
    for evt in finished:
        rollup = evt["payload"]["tool_call"].get("phase_totals_rollup") or {}
        # Each task recorded exactly 2 phases — their rollup must reflect
        # ONLY the durations its own context recorded, never another task's.
        assert set(rollup.keys()) == {"queued", "exec"}
        tool_use_id = evt["payload"]["tool_call"]["tool_use_id"]
        expected = float(10 + int(tool_use_id.split("-")[1]))
        assert rollup["queued"] == pytest.approx(expected, abs=0.001)
        assert rollup["exec"] == pytest.approx(expected, abs=0.001)
