"""Tests for the runner-side DaemonAuditPuller."""

from __future__ import annotations

import asyncio

import pytest

from task_center_runner.audit.daemon_pull import (
    DaemonAuditPuller,
    PRESSURE_ESCALATION_STREAK,
)


def _response(
    events: list[dict[str, object]],
    *,
    after_seq: int,
    boot_epoch_id: int,
    pressure: float = 0.0,
    dropped: int = 0,
    lost_before_seq: int = 0,
) -> dict[str, object]:
    return {
        "schema": "sandbox.daemon.audit.pull.v1",
        "cursor": {"after_seq": after_seq, "lost_before_seq": lost_before_seq},
        "buffer": {
            "retained_events": len(events),
            "retained_bytes": 0,
            "max_events": 50_000,
            "max_bytes": 8 * 1024 * 1024,
            "pressure": pressure,
            "dropped_event_count": dropped,
            "dropped_event_count_by_lane": {},
            "lost_before_seq": lost_before_seq,
        },
        "snapshot": {"daemon": {"boot_epoch_id": boot_epoch_id, "next_seq": after_seq + 1}},
        "events": events,
        "success": True,
    }


async def _make_puller(
    pull_results,
    *,
    emitted: list[list[dict[str, object]]] | None = None,
) -> DaemonAuditPuller:
    if emitted is None:
        emitted = []

    iterator = iter(pull_results)

    async def fake_pull(after_seq: int, limit: int) -> dict[str, object]:
        try:
            return next(iterator)
        except StopIteration:
            return _response([], after_seq=after_seq, boot_epoch_id=1)

    def emit(events: list[dict[str, object]], _resp: dict[str, object]) -> None:
        emitted.append(events)

    return DaemonAuditPuller(fake_pull, emit=emit, floor_ms=10)


@pytest.mark.asyncio
async def test_puller_final_drain_before_recorder_dispose() -> None:
    """stop() must drain remaining events before returning."""
    emitted: list[list[dict[str, object]]] = []
    seq_counter = 0

    async def fake_pull(after_seq: int, limit: int) -> dict[str, object]:
        nonlocal seq_counter
        if seq_counter >= 3:
            return _response([], after_seq=seq_counter - 1, boot_epoch_id=1)
        seq_counter += 1
        return _response(
            [{"seq": seq_counter - 1, "type": "x", "payload": {}, "lane": "normal"}],
            after_seq=seq_counter - 1,
            boot_epoch_id=1,
        )

    def emit(events, _r):
        emitted.append(events)

    puller = DaemonAuditPuller(fake_pull, emit=emit, floor_ms=10)
    puller.start()
    await asyncio.sleep(0.05)
    await puller.stop()

    assert sum(len(batch) for batch in emitted) == 3


@pytest.mark.asyncio
async def test_puller_floor_raises_under_sustained_pressure() -> None:
    """3 consecutive pulls with pressure>0.8 trigger floor escalation."""
    emitted: list[list[dict[str, object]]] = []
    calls = [
        _response([], after_seq=-1, boot_epoch_id=1, pressure=0.9),
        _response([], after_seq=-1, boot_epoch_id=1, pressure=0.9),
        _response([], after_seq=-1, boot_epoch_id=1, pressure=0.9),
    ]
    iterator = iter(calls)

    async def fake_pull(after_seq: int, limit: int) -> dict[str, object]:
        try:
            return next(iterator)
        except StopIteration:
            return _response([], after_seq=after_seq, boot_epoch_id=1, pressure=0.1)

    def emit(events, _r):
        emitted.append(events)

    puller = DaemonAuditPuller(fake_pull, emit=emit, floor_ms=100)
    initial = puller.floor_ms
    # Drive PRESSURE_ESCALATION_STREAK pulls directly.
    for _ in range(PRESSURE_ESCALATION_STREAK):
        await puller._pull_once()
    assert puller.floor_ms > initial
    assert puller.stats.floor_raises >= 1


@pytest.mark.asyncio
async def test_puller_floor_never_lowers_automatically() -> None:
    """After escalation, dropping pressure leaves floor raised."""
    async def fake_pull(after_seq: int, limit: int) -> dict[str, object]:
        return _response([], after_seq=after_seq, boot_epoch_id=1, pressure=0.0)

    puller = DaemonAuditPuller(fake_pull, emit=lambda *_: None, floor_ms=100)
    puller._floor_ms = 500
    puller._stats.max_buffer_pressure = 0.9
    # Now pressure recedes to 0; many pulls in a row.
    for _ in range(5):
        await puller._pull_once()
    assert puller.floor_ms == 500


@pytest.mark.asyncio
async def test_puller_reset_floor_returns_to_default() -> None:
    async def fake_pull(after_seq: int, limit: int) -> dict[str, object]:
        return _response([], after_seq=after_seq, boot_epoch_id=1)

    puller = DaemonAuditPuller(fake_pull, emit=lambda *_: None, floor_ms=100)
    puller._floor_ms = 800
    puller.reset_floor()
    assert puller.floor_ms == 100


@pytest.mark.asyncio
async def test_daemon_restart_epoch_handled_by_puller() -> None:
    """boot_epoch_id change resets cursor and synthesizes daemon.restart_observed."""
    emitted: list[list[dict[str, object]]] = []
    calls = [
        _response(
            [{"seq": 0, "type": "x", "payload": {}, "lane": "normal"}],
            after_seq=0,
            boot_epoch_id=1,
        ),
        # Epoch change.
        _response(
            [{"seq": 0, "type": "y", "payload": {}, "lane": "normal"}],
            after_seq=0,
            boot_epoch_id=2,
        ),
    ]
    iterator = iter(calls)

    async def fake_pull(after_seq: int, limit: int) -> dict[str, object]:
        try:
            return next(iterator)
        except StopIteration:
            return _response([], after_seq=after_seq, boot_epoch_id=2)

    def emit(events, _r):
        emitted.append(events)

    puller = DaemonAuditPuller(fake_pull, emit=emit, floor_ms=10)
    await puller._pull_once()
    await puller._pull_once()

    flat = [ev for batch in emitted for ev in batch]
    types = [ev.get("type") for ev in flat]
    assert "daemon.restart_observed" in types
    assert puller.stats.daemon_restarts_observed == 1


@pytest.mark.asyncio
async def test_puller_never_blocks_on_pull_failure() -> None:
    """A raising pull() increments error count and the puller keeps going."""
    async def fake_pull(after_seq: int, limit: int) -> dict[str, object]:
        raise RuntimeError("transport closed")

    puller = DaemonAuditPuller(fake_pull, emit=lambda *_: None, floor_ms=10)
    await puller._pull_once()
    assert puller.stats.pull_error_count == 1
