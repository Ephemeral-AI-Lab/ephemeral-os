"""Bound B: mount time grows slowly with manifest depth and disk stays flat."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ..._harness.lease_resource_probe import (
    MOUNT_SYSCALLS,
    ShellTelemetry,
    assert_mount_slope_by_depth,
    build_layer_stack,
    fail_if_depth_errors,
    has_cap_sys_admin,
    run_shell_batch,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not has_cap_sys_admin(),
    reason="O(1) overlay verification requires Linux with private mount namespaces",
)

_FIXED_N = 10
_MANIFEST_DEPTHS = (2, 10, 50, 100, 110)
_COMMAND = "test -f known_file.bin"


def test_bound_b_manifest_depth_sweep(tmp_path: Path) -> None:
    """Run every M before failing so one bad depth does not hide later depths."""
    asyncio.run(_run_bound_b(tmp_path))


async def _run_bound_b(tmp_path: Path) -> None:
    rows_by_depth: dict[int, list[ShellTelemetry]] = {}
    errors: dict[int, BaseException] = {}

    for depth in _MANIFEST_DEPTHS:
        try:
            case_root = tmp_path / f"M{depth}"
            stack = build_layer_stack(case_root, manifest_depth=depth)
            rows_by_depth[depth] = await run_shell_batch(
                stack=stack,
                workspace_root=case_root / "workspace-root",
                writable_root=case_root / "overlay-writable-root",
                requested_path=MOUNT_SYSCALLS,
                commands=[_COMMAND] * _FIXED_N,
                request_prefix=f"mount-syscalls-M{depth}",
            )
        except BaseException as exc:
            errors[depth] = exc
            continue

    fail_if_depth_errors(errors, label="Bound B")
    assert_mount_slope_by_depth(rows_by_depth)
