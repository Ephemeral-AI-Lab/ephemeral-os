"""E1, E1.1 — overlay mount depth via direct mount(2) syscall.

Backs §4.2. Pass bar: ``mount(2)`` rc=0 at every depth in {1..200};
util-linux ``mount(8)`` documented as failing at depth ≥ 10. Reuses
historical baseline from ``.omc/results/stack-overlay-live-*.jsonl``.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs overlay_sandbox fixture wiring (register_overlay_client) and direct-syscall mount probe helper in _harness"


def test_direct_syscall_mount_at_depths_1_5_10_30_50_80_100_200(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_mount8_binary_negative_control_fails_at_depth_ge_10(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_unshare_Urm_namespace_isolation(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
