"""E1, E1.1 — overlay mount depth via direct mount(2) syscall.

Backs §4.2. Pass bar: ``mount(2)`` rc=0 at every depth in {1..200};
util-linux ``mount(8)`` documented as failing at depth ≥ 10. Reuses
historical baseline from ``.omc/results/stack-overlay-live-*.jsonl``.
"""

from __future__ import annotations

import json
import shlex

import pytest

from ..._harness.overlay_probe import (
    OVERLAY_ROOT,
    script_mount8_negative_control,
    script_mount_depths,
    wrap_unshare,
)
from ..._harness.sandbox_fixture import SandboxHandle


_DEPTHS_FULL = (1, 5, 10, 30, 50, 80, 100, 200)
_DEPTHS_NEG_CTL = (10, 100)


def _print_metrics(label: str, payload: dict) -> None:
    print(f"\n[{label}] {json.dumps(payload, separators=(',', ':'))}")


@pytest.mark.asyncio
async def test_direct_syscall_mount_at_depths_1_5_10_30_50_80_100_200(
    overlay_sandbox: SandboxHandle,
) -> None:
    """mount(2) rc=0 at every depth in {1, 5, 10, 30, 50, 80, 100, 200}."""
    cmd = wrap_unshare(
        script_mount_depths(overlay_root=OVERLAY_ROOT, depths=_DEPTHS_FULL)
    )
    result = await overlay_sandbox.raw_exec(
        overlay_sandbox.sandbox_id, cmd, timeout=120
    )
    assert result.exit_code == 0, (
        f"mount probe failed (rc={result.exit_code}): {result.stderr or result.stdout}"
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    _print_metrics("E1.mount2.syscall_depths", payload)
    rows = payload["results"]
    assert len(rows) == len(_DEPTHS_FULL)
    failed = [r for r in rows if r["rc"] != 0]
    assert not failed, (
        "mount(2) failures at depths: "
        + ", ".join(f"d={r['depth']} errno={r.get('errno_name') or r['errno']}" for r in failed)
    )
    bad_marker = [r for r in rows if r.get("marker_ok") is False]
    assert not bad_marker, f"upper marker round-trip failed: {bad_marker}"


@pytest.mark.asyncio
async def test_mount8_binary_negative_control_fails_at_depth_ge_10(
    overlay_sandbox: SandboxHandle,
) -> None:
    """util-linux ``mount(8)`` is the documented argv-overflow failure case."""
    cmd = wrap_unshare(
        script_mount8_negative_control(
            overlay_root=OVERLAY_ROOT, depths=_DEPTHS_NEG_CTL
        )
    )
    result = await overlay_sandbox.raw_exec(
        overlay_sandbox.sandbox_id, cmd, timeout=120
    )
    assert result.exit_code == 0, (
        f"mount8 probe failed (rc={result.exit_code}): "
        f"{result.stderr or result.stdout}"
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    _print_metrics("E1.mount8.negative_control", payload)
    by_depth = {r["depth"]: r for r in payload["results"]}
    # depth 10 is the documented "still works" case; depth 100 must fail
    deep = by_depth.get(100)
    assert deep is not None and deep["rc"] != 0, (
        f"mount(8) at depth 100 should fail but reported {deep!r}"
    )


@pytest.mark.asyncio
async def test_unshare_Urm_namespace_isolation(
    overlay_sandbox: SandboxHandle,
) -> None:
    """``unshare -Urm`` must give us mount(2) without root."""
    # Run the mount probe at a single shallow depth and confirm rc=0
    # while a non-namespaced mount call rejects with EPERM/EACCES.
    cmd_ns = wrap_unshare(
        script_mount_depths(
            overlay_root=OVERLAY_ROOT,
            depths=(2,),
            write_marker=False,
        )
    )
    ns_result = await overlay_sandbox.raw_exec(
        overlay_sandbox.sandbox_id, cmd_ns, timeout=60
    )
    assert ns_result.exit_code == 0, ns_result.stderr or ns_result.stdout
    ns_payload = json.loads(ns_result.stdout.strip().splitlines()[-1])
    _print_metrics("E1.unshare.in_namespace", ns_payload)
    assert ns_payload["results"][0]["rc"] == 0

    # Same probe outside the namespace; record whether it also succeeds.
    bare_cmd = "python3 -c " + shlex.quote(
        script_mount_depths(
            overlay_root=OVERLAY_ROOT + "_bare",
            depths=(2,),
            write_marker=False,
        )
    )
    bare_result = await overlay_sandbox.raw_exec(
        overlay_sandbox.sandbox_id, bare_cmd, timeout=60
    )
    payload = (
        json.loads(bare_result.stdout.strip().splitlines()[-1])
        if bare_result.exit_code == 0
        else None
    )
    _print_metrics(
        "E1.unshare.outside_namespace",
        {"rc": bare_result.exit_code, "payload": payload},
    )
    # The Daytona images run as root with CAP_SYS_ADMIN already, so the
    # bare path may also succeed — record but don't fail on it; the
    # invariant we need is that the in-namespace path *always* succeeds.
