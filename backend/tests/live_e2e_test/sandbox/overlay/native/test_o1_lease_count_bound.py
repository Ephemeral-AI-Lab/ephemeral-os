"""Bound A: lower-side disk is O(1) per lease under the new mount API."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ..._harness.lease_resource_probe import (
    NEW_MOUNT_API,
    assert_new_api_o1_bounds,
    build_layer_stack,
    has_cap_sys_admin,
    run_shell_batch,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not has_cap_sys_admin(),
    reason="O(1) overlay verification requires Linux with private mount namespaces",
)

_LEASE_COUNTS = (1, 10, 50, 100, 200)
_FIXED_MANIFEST_DEPTH = 10
_COMMAND = "test -f known_file.bin"


def test_bound_a_lease_count_sweep(tmp_path: Path) -> None:
    """Assert O(1) lower-side disk for N active shell leases."""
    asyncio.run(_run_bound_a(tmp_path))


async def _run_bound_a(tmp_path: Path) -> None:
    for n in _LEASE_COUNTS:
        case_root = tmp_path / f"N{n}"
        stack = build_layer_stack(
            case_root,
            manifest_depth=_FIXED_MANIFEST_DEPTH,
            base_payload_bytes=8 * 1024 * 1024,
        )
        commands = [_COMMAND] * n

        new_api = await run_shell_batch(
            stack=stack,
            workspace_root=case_root / "workspace-root",
            scratch_root=case_root / "scratch-new-api",
            requested_path=NEW_MOUNT_API,
            commands=commands,
            request_prefix=f"new-api-N{n}",
        )
        assert_new_api_o1_bounds(new_api)
