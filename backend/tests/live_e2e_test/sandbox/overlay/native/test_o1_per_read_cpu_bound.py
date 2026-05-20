"""Bound C: successful file reads have bounded CPU slope by manifest depth."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ..._harness.lease_resource_probe import (
    NEW_MOUNT_API,
    ShellTelemetry,
    assert_read_cpu_slope_by_depth,
    build_layer_stack,
    fail_if_depth_errors,
    has_cap_sys_admin,
    run_shell_batch,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not has_cap_sys_admin(),
    reason="O(1) overlay verification requires Linux with private mount namespaces",
)

_MANIFEST_DEPTHS = (2, 10, 50, 100, 110)
_READ_REPEATS = 3
_COMMAND = "cat known_file.bin >/dev/null"


def test_bound_c_per_read_cpu_slope(tmp_path: Path) -> None:
    """Use command-exec child CPU telemetry for successful bottom-layer reads."""
    asyncio.run(_run_bound_c(tmp_path))


async def _run_bound_c(tmp_path: Path) -> None:
    rows_by_depth: dict[int, list[ShellTelemetry]] = {}
    errors: dict[int, BaseException] = {}

    for depth in _MANIFEST_DEPTHS:
        try:
            case_root = tmp_path / f"M{depth}"
            stack = build_layer_stack(
                case_root,
                manifest_depth=depth,
                base_payload_bytes=1024,
            )
            rows_by_depth[depth] = await run_shell_batch(
                stack=stack,
                workspace_root=case_root / "workspace-root",
                scratch_root=case_root / "scratch-new-api",
                requested_path=NEW_MOUNT_API,
                commands=[_COMMAND] * _READ_REPEATS,
                request_prefix=f"read-M{depth}",
            )
        except BaseException as exc:
            errors[depth] = exc
            continue

    fail_if_depth_errors(errors, label="Bound C")
    assert_read_cpu_slope_by_depth(rows_by_depth)
