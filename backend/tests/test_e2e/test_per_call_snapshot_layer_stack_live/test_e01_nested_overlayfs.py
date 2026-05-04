from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    depth_matrix,
    overlay_probe_command,
    parse_json_line,
    print_live_metric,
    require_commands,
    run_live_command,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e01_nested_overlayfs_viable_inside_daytona(live_snapshot_sandbox):
    await require_commands(live_snapshot_sandbox, "bash", "python3", "unshare")
    depths = depth_matrix()
    result = await run_live_command(
        live_snapshot_sandbox,
        overlay_probe_command(depths=depths, iterations=1, write_check=True),
        timeout=180,
        label="e01.nested_overlayfs",
    )
    assert_success(result)
    payload = parse_json_line(result.stdout)
    failures = [entry for entry in payload["depths"] if entry["error"]]
    print_live_metric("e01.summary", depths=payload["depths"], failures=len(failures))
    assert not failures
