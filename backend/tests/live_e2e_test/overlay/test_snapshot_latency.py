"""E2 — snapshot mount latency.

Backs §4.2. Pass bar: p99 < 5 ms at depth 100; 0 failures across 1000
iterations × 8 depths.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs overlay_sandbox + latency-histogram helper"


def test_p99_mount_under_5ms_at_depth_100(overlay_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_depth_200_overshoot_probe_records_latency(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_1000_iter_zero_failures_per_depth(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
