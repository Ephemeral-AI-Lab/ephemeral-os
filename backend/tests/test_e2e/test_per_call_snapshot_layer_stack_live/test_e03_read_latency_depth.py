from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    depth_matrix,
    full_experiment_enabled,
    overlay_probe_command,
    parse_json_line,
    print_live_metric,
    require_commands,
    run_live_command,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e03_warm_read_latency_vs_depth(live_snapshot_sandbox):
    await require_commands(live_snapshot_sandbox, "bash", "python3", "unshare")
    depths = depth_matrix()
    read_files = 10_000 if full_experiment_enabled() else 500
    result = await run_live_command(
        live_snapshot_sandbox,
        overlay_probe_command(depths=depths, iterations=1, read_files=read_files),
        timeout=900 if full_experiment_enabled() else 180,
        label="e03.read_latency_depth",
    )
    assert_success(result)
    payload = parse_json_line(result.stdout)
    failures = [entry for entry in payload["depths"] if entry["error"]]
    print_live_metric("e03.summary", read_files=read_files, depths=payload["depths"])
    assert not failures

    for entry in payload["depths"]:
        first, second = entry["read_passes"]
        assert first["files"] == read_files
        assert second["files"] == read_files
        assert first["bytes"] == second["bytes"]
