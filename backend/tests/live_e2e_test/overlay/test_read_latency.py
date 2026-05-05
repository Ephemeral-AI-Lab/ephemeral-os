"""E3 — read latency through overlay snapshot.

Backs §4.2. Pass bar: warm read at depth 100 within 2× baseline; cold
read at depth 50 within 5× baseline (or skipped with explicit reason if
``drop_caches`` is denied).
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs overlay_sandbox + read-latency helper"


def test_warm_read_at_depth_100_within_2x_baseline(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_cold_read_at_depth_50_within_5x_baseline_or_skipped_with_reason(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
