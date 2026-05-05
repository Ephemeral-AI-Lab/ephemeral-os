"""E6 — layer GC and lease interaction.

Backs §4.1. Pass bar: 0 false-frees over 100 runs; retired-but-pinned
layers reclaimed exactly once on lease release.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs lease/squash workload helpers in _harness.workload"


def test_leased_layer_not_gced_until_release(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_unreferenced_squashed_layer_freed_within_one_sweep(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_pinned_layer_survives_squash_until_lease_drops(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
