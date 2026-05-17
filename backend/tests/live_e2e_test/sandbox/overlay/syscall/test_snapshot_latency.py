"""E2 — snapshot mount latency.

Backs §4.2. Pass bar: p99 < 5 ms at depth 100; 0 failures across 1000
iterations × 8 depths.
"""

from __future__ import annotations

import json
import os

import pytest

from ..._harness.overlay_probe import (
    OVERLAY_ROOT,
    script_snapshot_latency,
    wrap_unshare,
)
from ..._harness.sandbox_fixture import SandboxHandle


_DEPTHS = (1, 5, 10, 30, 50, 80, 100, 200)
_P99_BUDGET_MS_AT_100 = 5.0


def _iterations(default: int) -> int:
    """Allow shrinking iteration count via env without losing the budget shape."""
    raw = os.environ.get("EPHEMERALOS_OVERLAY_LATENCY_ITERATIONS", "").strip()
    if not raw:
        return default
    value = int(raw)
    return max(50, value)


def _print_metrics(label: str, payload: dict) -> None:
    print(f"\n[{label}] {json.dumps(payload, separators=(',', ':'))}")


async def _run_latency_probe(
    handle: SandboxHandle, *, iterations: int
) -> dict:
    cmd = wrap_unshare(
        script_snapshot_latency(
            overlay_root=OVERLAY_ROOT,
            depths=_DEPTHS,
            iterations=iterations,
        )
    )
    result = await handle.raw_exec(handle.sandbox_id, cmd, timeout=600)
    assert result.exit_code == 0, (
        f"snapshot latency probe failed (rc={result.exit_code}): "
        f"{result.stderr or result.stdout}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.asyncio
async def test_p99_mount_under_5ms_at_depth_100(
    overlay_sandbox: SandboxHandle,
) -> None:
    """p99 mount(2) latency must stay under 5 ms at depth 100."""
    payload = await _run_latency_probe(
        overlay_sandbox, iterations=_iterations(1000)
    )
    _print_metrics("E2.snapshot_latency", payload)
    by_depth = {row["depth"]: row for row in payload["results"]}
    row100 = by_depth[100]
    assert row100["failures"] == 0, row100
    assert row100["p99_ms"] < _P99_BUDGET_MS_AT_100, (
        f"depth=100 p99={row100['p99_ms']:.3f}ms exceeds "
        f"budget {_P99_BUDGET_MS_AT_100}ms (full row: {row100})"
    )


@pytest.mark.asyncio
async def test_depth_200_overshoot_probe_records_latency(
    overlay_sandbox: SandboxHandle,
) -> None:
    """Depth 200 still mounts and reports a latency distribution."""
    iterations = _iterations(200)
    payload = await _run_latency_probe(overlay_sandbox, iterations=iterations)
    _print_metrics("E2.depth_200_overshoot", payload)
    by_depth = {row["depth"]: row for row in payload["results"]}
    row200 = by_depth[200]
    assert row200["iterations"] == iterations
    assert row200["failures"] == 0, row200
    assert row200["p99_ms"] > 0.0


@pytest.mark.asyncio
async def test_1000_iter_zero_failures_per_depth(
    overlay_sandbox: SandboxHandle,
) -> None:
    """Across all eight depths, mount(2) must never fail."""
    iterations = _iterations(1000)
    payload = await _run_latency_probe(overlay_sandbox, iterations=iterations)
    _print_metrics("E2.zero_failures_per_depth", payload)
    bad = [row for row in payload["results"] if row["failures"]]
    assert not bad, (
        "depths reporting failures: "
        + ", ".join(
            f"d={r['depth']} fails={r['failures']} first_errno={r['first_errno']}"
            for r in bad
        )
    )
    # Print a flat performance summary that the user can eyeball.
    summary = {
        "iterations_per_depth": iterations,
        "depths": [
            {
                "d": r["depth"],
                "p50": round(r["p50_ms"], 4),
                "p95": round(r["p95_ms"], 4),
                "p99": round(r["p99_ms"], 4),
                "max": round(r["max_ms"], 4),
                "options_len": r["options_len"],
            }
            for r in payload["results"]
        ],
    }
    _print_metrics("E2.summary", summary)
