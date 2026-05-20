"""Memory bound for concurrent command-exec leases on the new mount API."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ..._harness.lease_resource_probe import (
    NEW_MOUNT_API,
    assert_memory_bound,
    build_layer_stack,
    has_cap_sys_admin,
    run_shell_batch,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not has_cap_sys_admin(),
    reason="O(1) overlay verification requires Linux with private mount namespaces",
)

_FIXED_MANIFEST_DEPTH = 10
_COMMAND = "test -f known_file.bin"


def test_bound_d_memory_per_lease(tmp_path: Path) -> None:
    """Assert RSS growth from N=1 to N=200 stays <= 2 MiB per lease."""
    asyncio.run(_run_memory_bound(tmp_path))


async def _run_memory_bound(tmp_path: Path) -> None:
    stack = build_layer_stack(tmp_path, manifest_depth=_FIXED_MANIFEST_DEPTH)
    n1 = await run_shell_batch(
        stack=stack,
        workspace_root=tmp_path / "workspace-root",
        scratch_root=tmp_path / "scratch-N1",
        requested_path=NEW_MOUNT_API,
        commands=[_COMMAND],
        request_prefix="memory-N1",
    )
    n200 = await run_shell_batch(
        stack=stack,
        workspace_root=tmp_path / "workspace-root",
        scratch_root=tmp_path / "scratch-N200",
        requested_path=NEW_MOUNT_API,
        commands=[_COMMAND] * 200,
        request_prefix="memory-N200",
    )

    assert_memory_bound(n1=n1, n200=n200)
