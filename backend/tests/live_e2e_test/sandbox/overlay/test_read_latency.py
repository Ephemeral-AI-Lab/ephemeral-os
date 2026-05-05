"""E3 — read latency through overlay snapshot.

Backs §4.2. Pass bar: warm read at depth 100 within 2× baseline; cold
read at depth 50 within 5× baseline (or skipped with explicit reason if
``drop_caches`` is denied).
"""

from __future__ import annotations

import json

import pytest

from .._harness.overlay_probe import (
    OVERLAY_ROOT,
    script_read_latency,
    wrap_unshare,
)
from .._harness.sandbox_fixture import SandboxHandle


_DEPTHS = (1, 5, 10, 30, 50, 80, 100)
_FILES_PER_DEPTH = 256
_BYTES_PER_FILE = 256


def _print_metrics(label: str, payload: dict) -> None:
    print(f"\n[{label}] {json.dumps(payload, separators=(',', ':'))}")


async def _run_read_probe(handle: SandboxHandle) -> dict:
    cmd = wrap_unshare(
        script_read_latency(
            overlay_root=OVERLAY_ROOT,
            depths=_DEPTHS,
            files_per_depth=_FILES_PER_DEPTH,
            bytes_per_file=_BYTES_PER_FILE,
        )
    )
    result = await handle.raw_exec(handle.sandbox_id, cmd, timeout=300)
    assert result.exit_code == 0, (
        f"read latency probe failed (rc={result.exit_code}): "
        f"{result.stderr or result.stdout}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.asyncio
async def test_warm_read_at_depth_100_within_2x_baseline(
    overlay_sandbox: SandboxHandle,
) -> None:
    payload = await _run_read_probe(overlay_sandbox)
    _print_metrics("E3.read_latency", payload)
    by_depth = {row["depth"]: row for row in payload["depths"]}
    row100 = by_depth[100]
    ratio = row100.get("warm_vs_depth1")
    assert ratio is not None, f"missing warm_vs_depth1: {row100}"
    assert ratio < 2.0, (
        f"depth=100 warm_read_ms={row100['warm_read_ms']:.3f} "
        f"warm_vs_depth1={ratio:.3f} exceeds 2× baseline (row={row100})"
    )


@pytest.mark.asyncio
async def test_cold_read_at_depth_50_within_5x_baseline_or_skipped_with_reason(
    overlay_sandbox: SandboxHandle,
) -> None:
    payload = await _run_read_probe(overlay_sandbox)
    by_depth = {row["depth"]: row for row in payload["depths"]}
    row50 = by_depth[50]
    drop = row50["drop_caches"]
    if not drop["supported"]:
        pytest.skip(
            f"cold-read measurement requires drop_caches: {drop['error']}"
        )
    base_cold = next(
        (r["cold_read_ms"] for r in payload["depths"] if r["depth"] == 1),
        None,
    )
    assert base_cold, f"missing baseline cold read: {payload['depths']}"
    cold = row50["cold_read_ms"]
    assert cold is not None, row50
    ratio = cold / base_cold
    _print_metrics(
        "E3.cold_read",
        {"depth": 50, "cold_read_ms": cold, "ratio_vs_depth1": ratio},
    )
    assert ratio < 5.0, (
        f"depth=50 cold_read_ms={cold:.3f} ratio={ratio:.3f} exceeds 5× baseline"
    )
