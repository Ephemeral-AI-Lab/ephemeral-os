"""E12 — lease-budget caps fire deterministically.

Backs §4.1. Pass bar: caps fire deterministically; no GC starvation;
kill semantics consistent across all four caps.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs _harness.with_thresholds(MAX_LEASE_AGE=..., PER_SESSION_PIN_BYTES=..., MAX_PINNED_OLD_MANIFESTS=..., GLOBAL_PIN_BYTES=...)"


def test_max_lease_age_force_kills_shell(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_per_session_pin_bytes_blocks_new_writers(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_max_pinned_old_manifests_evicts_oldest(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_global_pin_bytes_evicts_longest_pinning_session(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
