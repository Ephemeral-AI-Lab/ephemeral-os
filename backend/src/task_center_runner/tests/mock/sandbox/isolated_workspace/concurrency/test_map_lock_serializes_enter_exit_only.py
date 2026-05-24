"""``_map_lock`` is enter/exit-only — tool_calls for different agents overlap.

The map lock guards the ``_handles`` dict mutation, not the run_in_handle
critical section. Two agents' concurrent tool_calls should interleave —
provable through audit event ordering at the ``duration_s`` scale.
"""

from __future__ import annotations

import asyncio

import pytest

from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from task_center_runner.tests.mock.sandbox.isolated_workspace import (
    _iws_invariants,
    _iws_rpc,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not database_configured(), reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(240)
async def test_map_lock_serializes_enter_exit_only(
    iws_clean_sandbox, iws_audit_jsonl,
) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    a, b = await asyncio.gather(
        _iws_rpc.enter(sandbox_id, "agent-A", layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT),
        _iws_rpc.enter(sandbox_id, "agent-B", layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT),
    )
    assert a.get("success") and b.get("success"), (a, b)
    try:
        # Long-ish sleeps so the overlap window is large compared to RPC
        # round-trip jitter. With no per-handle execution lock, total wall ≈
        # max(0.7s, 0.7s) ≈ 0.7s, NOT 1.4s.
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        res = await asyncio.gather(
            _iws_rpc.shell(sandbox_id, "agent-A", "sleep 0.7"),
            _iws_rpc.shell(sandbox_id, "agent-B", "sleep 0.7"),
        )
        wall = loop.time() - t0
        assert all(r.get("success") for r in res), res
        jsonl = await iws_audit_jsonl()
        tool_calls = _iws_invariants.events_of_type(
            jsonl, "sandbox_isolated_workspace_tool_call",
        )
        recent_tool_calls = tool_calls[-2:]
        durations = [
            float((row.get("payload") or {}).get("duration_s", 0.0))
            for row in recent_tool_calls
        ]
        # Compare against observed daemon durations instead of a fixed wall
        # threshold. Docker Desktop can add hundreds of ms of setns/exec
        # overhead per call; serialization still shows up as wall ~= sum.
        assert len(recent_tool_calls) == 2, tool_calls
        assert all(d >= 0.6 for d in durations), durations
        assert wall < sum(durations) * 0.75, (
            "two agents' tool_calls should overlap materially; "
            f"wall={wall:.2f}s durations={durations!r}",
        )
        intervals = []
        for row, duration in zip(recent_tool_calls, durations, strict=True):
            completed_at = float(row.get("ts") or 0.0)
            intervals.append((completed_at - duration, completed_at))
        overlap_s = min(end for _, end in intervals) - max(start for start, _ in intervals)
        assert overlap_s >= 0.4, (
            "audit intervals should overlap at sleep-scale duration",
            intervals,
        )
        handle_ids = {
            (row.get("payload") or {}).get("handle_id")
            for row in recent_tool_calls
        }
        # Both handles emitted a tool_call event.
        assert len(handle_ids) == 2, recent_tool_calls
    finally:
        await _iws_rpc.exit_(sandbox_id, "agent-A")
        await _iws_rpc.exit_(sandbox_id, "agent-B")
