"""E2.1 — concurrent-mount capacity probe.

Backs §4.2. Measures the number of overlay mounts the kernel can sustain
*simultaneously* at depth 50 by holding N mounts open before umounting in
LIFO order. Records per-mount latency, proc/self/mounts read time, and
the failure/errno breakdown when capacity is exhausted.

Pass bar: N=100 mounts must succeed with 0 failures; mount p99 at N=100
stays within 5× the single-mount baseline (depth 50, ~0.07 ms p50 in
``test_snapshot_latency``).
"""

from __future__ import annotations

import json
import os

import pytest

from .._harness.overlay_probe import (
    OVERLAY_ROOT,
    script_concurrent_mounts,
    wrap_unshare,
)
from .._harness.sandbox_fixture import SandboxHandle


_DEFAULT_COUNTS = (10, 50, 100, 200)
_DEPTH = 50
_PASS_BAR_N = 100
_PASS_BAR_P99_MS = 5.0  # depth=50 single-mount p99 was ~0.25ms; 5ms is 20× headroom


def _print_metrics(label: str, payload: dict) -> None:
    print(f"\n[{label}] {json.dumps(payload, separators=(',', ':'))}")


def _counts() -> tuple[int, ...]:
    raw = os.environ.get("EPHEMERALOS_OVERLAY_CONCURRENT_COUNTS", "").strip()
    if not raw:
        return _DEFAULT_COUNTS
    return tuple(int(part) for part in raw.split(",") if part.strip())


@pytest.mark.asyncio
async def test_holds_n_concurrent_mounts_at_depth_50(
    overlay_sandbox: SandboxHandle,
) -> None:
    counts = _counts()
    cmd = wrap_unshare(
        script_concurrent_mounts(
            overlay_root=OVERLAY_ROOT, counts=counts, depth=_DEPTH
        )
    )
    result = await overlay_sandbox.raw_exec(
        overlay_sandbox.sandbox_id, cmd, timeout=300
    )
    assert result.exit_code == 0, (
        f"concurrent-mount probe failed (rc={result.exit_code}): "
        f"{result.stderr or result.stdout}"
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    _print_metrics("E2.1.concurrent_mounts", payload)

    by_n = {row["n_target"]: row for row in payload["results"]}
    summary = [
        {
            "n": r["n_target"],
            "ok": r["n_mounted"],
            "fail": r["failures"],
            "first_err": r["first_errno"],
            "p50_ms": round(r["mount_p50_ms"], 4),
            "p99_ms": round(r["mount_p99_ms"], 4),
            "max_ms": round(r["mount_max_ms"], 4),
            "proc_mounts_ms": round(r["proc_mounts_read_ms"], 4),
            "proc_overlay_lines": r["proc_overlay_lines"],
        }
        for r in payload["results"]
    ]
    _print_metrics("E2.1.summary", {"depth": _DEPTH, "rows": summary})

    # Pass bar: N=100 must reach full mount count with bounded p99.
    if _PASS_BAR_N in by_n:
        row = by_n[_PASS_BAR_N]
        assert row["failures"] == 0, row
        assert row["n_mounted"] == _PASS_BAR_N, row
        assert row["mount_p99_ms"] < _PASS_BAR_P99_MS, (
            f"N={_PASS_BAR_N} mount p99={row['mount_p99_ms']:.3f}ms "
            f"exceeds {_PASS_BAR_P99_MS}ms budget (row={row})"
        )

    # Whatever the largest N tested was, it must report a sane proc-mounts
    # snapshot (overlay line count >= n_mounted).
    largest = max(payload["results"], key=lambda r: r["n_target"])
    assert largest["proc_overlay_lines"] >= largest["n_mounted"], largest
