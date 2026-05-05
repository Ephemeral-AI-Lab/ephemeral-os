"""E5 — squash throughput pass bar.

Backs §4.1. Pass bar: depth stays in [40, 90] under 50/s for 5 min;
≤20 layers/s coalesce ratio. All cases skip until the harness exposes
``with_thresholds(MAX_DEPTH=..., SQUASH_TRIGGER=..., SQUASH_TARGET=...,
EMERGENCY_DEPTH=...)`` (plan §3.4).
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs _harness.with_thresholds() (plan §3.4)"


def test_sustained_50_commits_per_sec_keeps_depth_under_90(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_emergency_depth_triggers_foreground_squash(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_no_backpressure_in_normal_load(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
