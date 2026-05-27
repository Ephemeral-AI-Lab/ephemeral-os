"""Phase 1 — audit buffer + pull/snapshot RPC tests.

See `docs/daemon-audit-pull-consolidation-v3/phase-1-audit-buffer-and-pull-rpc.md`.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from sandbox.daemon.audit_buffer import (
    SCHEMA_VERSION,
    AuditBuffer,
)


def _event(kind: str = "noop", payload_size: int = 32) -> dict[str, Any]:
    return {"type": kind, "payload": {"filler": "x" * payload_size}}


def test_audit_buffer_ordering() -> None:
    buf = AuditBuffer(max_events=100, max_bytes=1 << 20)
    seqs = [buf.append(_event(), lane="normal") for _ in range(50)]
    assert seqs == list(range(50))
    assert all(
        e["seq"] < n["seq"]
        for e, n in zip(buf.pull(limit=50)["events"], buf.pull(limit=50)["events"][1:])
    )


def test_audit_buffer_eviction_events_and_bytes() -> None:
    buf = AuditBuffer(max_events=10, max_bytes=1 << 30)
    for _ in range(25):
        buf.append(_event(), lane="normal")
    snap = buf.snapshot()
    assert snap["buffer"]["retained_events"] == 10
    assert snap["buffer"]["dropped_event_count"] == 15

    big_payload = "x" * 4096
    buf2 = AuditBuffer(max_events=1_000_000, max_bytes=8 * 1024)
    for _ in range(20):
        buf2.append({"type": "big", "payload": {"f": big_payload}}, lane="normal")
    snap2 = buf2.snapshot()
    assert snap2["buffer"]["retained_bytes"] <= 8 * 1024
    assert snap2["buffer"]["dropped_event_count"] > 0


def test_audit_buffer_critical_lane_survives_sample_pressure() -> None:
    buf = AuditBuffer(max_events=100, max_bytes=1 << 20)
    crit_seqs: list[int] = []
    for i in range(10):
        crit_seqs.append(buf.append({"type": "crit", "i": i}, lane="critical"))
    for _ in range(500):
        buf.append({"type": "noise"}, lane="sample")
    events = buf.pull(limit=200)["events"]
    crit_present = {e["seq"] for e in events if e["lane"] == "critical"}
    assert set(crit_seqs) == crit_present
    snap = buf.snapshot()
    assert snap["buffer"]["dropped_event_count_by_lane"]["critical"] == 0
    assert snap["buffer"]["dropped_event_count_by_lane"]["sample"] > 0


def test_audit_buffer_pressure_formula() -> None:
    buf = AuditBuffer(max_events=10, max_bytes=1 << 30)
    for _ in range(5):
        buf.append(_event(), lane="normal")
    snap = buf.snapshot()
    assert snap["buffer"]["pressure"] == pytest.approx(0.5, abs=1e-6)


def test_pull_cursor_exclusive_and_drops_reported() -> None:
    buf = AuditBuffer(max_events=10, max_bytes=1 << 30)
    for _ in range(25):
        buf.append(_event(), lane="normal")
    result = buf.pull(after_seq=-1)
    assert result["schema"] == SCHEMA_VERSION
    first_seqs = [e["seq"] for e in result["events"]]
    assert first_seqs and all(s > -1 for s in first_seqs)
    assert result["buffer"]["dropped_event_count"] == 15
    assert result["buffer"]["lost_before_seq"] >= 15

    cursor = first_seqs[-1]
    result2 = buf.pull(after_seq=cursor)
    assert all(e["seq"] > cursor for e in result2["events"])


def test_snapshot_is_o1_under_load() -> None:
    buf = AuditBuffer(max_events=2000, max_bytes=1 << 30)
    for _ in range(10_000):
        buf.append(_event(payload_size=8), lane="normal")
    deadline = time.perf_counter()
    for _ in range(1000):
        buf.snapshot()
    elapsed = time.perf_counter() - deadline
    assert elapsed / 1000 < 0.001, f"snapshot p99 too slow: {elapsed/1000:.6f}s"


def test_schema_version_constant_matches_pull_response() -> None:
    buf = AuditBuffer()
    buf.append(_event(), lane="normal")
    pulled = buf.pull()
    assert pulled["schema"] == SCHEMA_VERSION
    assert buf.snapshot()["schema"] == SCHEMA_VERSION


def test_pressure_crossing_emits_daemon_event() -> None:
    buf = AuditBuffer(max_events=10, max_bytes=1 << 30, pressure_threshold=0.5)
    fired: list[dict[str, Any]] = []
    buf.register_pressure_cross_callback(lambda s: fired.append(s))
    for _ in range(5):
        buf.append(_event(), lane="normal")
    assert len(fired) == 1
    for _ in range(2):
        buf.append(_event(), lane="normal")
    assert len(fired) == 1  # edge-triggered, not re-fired


def test_audit_reset_floor_op_gated_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox.daemon.rpc.dispatcher import _audit_reset_floor_handler

    monkeypatch.delenv("EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET", raising=False)
    result = _audit_reset_floor_handler({})
    assert result["success"] is False
    assert result["error"]["kind"] == "forbidden"

    monkeypatch.setenv("EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET", "true")
    result_ok = _audit_reset_floor_handler({})
    assert result_ok["success"] is True


@pytest.mark.asyncio
async def test_audit_pull_and_snapshot_handlers_registered() -> None:
    from sandbox.daemon.audit_buffer import reset_audit_buffer_for_tests
    from sandbox.daemon.rpc.dispatcher import dispatch_envelope_async

    reset_audit_buffer_for_tests()
    snap = await dispatch_envelope_async({"op": "api.audit.snapshot", "args": {}})
    assert snap["success"] is True
    assert snap["schema"] == SCHEMA_VERSION
    pulled = await dispatch_envelope_async(
        {"op": "api.audit.pull", "args": {"after_seq": -1, "limit": 100}}
    )
    assert pulled["success"] is True
    assert pulled["schema"] == SCHEMA_VERSION
    assert isinstance(pulled["events"], list)


def test_phase_1_causal_chain_smoke() -> None:
    """Synthetic causal chain — one fake tool call + one isolated workspace lifecycle."""
    buf = AuditBuffer(max_events=200_000, max_bytes=64 * 1024 * 1024)
    op_id = "op-smoke-1"
    workspace_handle_id = "iws-smoke"

    def emit(event_type: str, lane: str, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "operation_id": op_id,
        }
        if extra:
            payload.update(extra)
        buf.append({"type": event_type, "payload": payload}, lane=lane)  # type: ignore[arg-type]

    emit("isolated_workspace.entered", "critical", {"workspace_handle_id": workspace_handle_id})
    emit(
        "tool_call.started",
        "normal",
        {"tool_name": "smoke_tool", "workspace_handle_id": workspace_handle_id},
    )
    emit("overlay_workspace.mounted", "critical", {})
    for phase in ("mount", "exec", "capture", "publish", "release"):
        emit("tool_call.phase", "sample", {"phase": phase, "tool_id": "tool-1"})
    emit("overlay_workspace.published", "critical", {})
    # Flood sample lane with throwaway events
    for _ in range(100_000):
        buf.append({"type": "noise"}, lane="sample")
    emit(
        "tool_call.finished",
        "normal",
        {
            "phase_totals_rollup": {
                "queued_ms": 1.0,
                "mount_ms": 2.0,
                "exec_ms": 3.0,
                "capture_ms": 1.0,
                "publish_ms": 1.0,
                "release_ms": 1.0,
            }
        },
    )
    emit(
        "isolated_workspace.exited",
        "critical",
        {
            "orphan_holder_count": 0,
            "holder_pid_alive": False,
        },
    )

    pulled = buf.pull(after_seq=-1, limit=200_000)
    crit_events = [
        e for e in pulled["events"] if e["lane"] == "critical"
    ]
    crit_types = [e["type"] for e in crit_events]
    expected_critical = {
        "isolated_workspace.entered",
        "overlay_workspace.mounted",
        "overlay_workspace.published",
        "isolated_workspace.exited",
    }
    assert expected_critical.issubset(set(crit_types))
    for e in crit_events:
        if e["type"] in expected_critical:
            assert e["payload"]["operation_id"] == op_id
    seqs = [e["seq"] for e in pulled["events"]]
    assert seqs == sorted(seqs)
    snap1 = buf.snapshot()
    snap2 = buf.snapshot()
    assert (
        snap1["snapshot"]["daemon"]["boot_epoch_id"]
        == snap2["snapshot"]["daemon"]["boot_epoch_id"]
    )
