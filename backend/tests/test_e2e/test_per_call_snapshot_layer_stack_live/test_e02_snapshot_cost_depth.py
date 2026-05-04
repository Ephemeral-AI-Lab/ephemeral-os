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


async def test_e02_direct_mount_cost_vs_depth(live_snapshot_sandbox):
    await require_commands(live_snapshot_sandbox, "bash", "python3", "unshare")
    iterations = 1000 if full_experiment_enabled() else 20
    depths = depth_matrix(include_depth_200=True)
    result = await run_live_command(
        live_snapshot_sandbox,
        overlay_probe_command(depths=depths, iterations=iterations),
        timeout=600 if full_experiment_enabled() else 180,
        label="e02.snapshot_cost_depth",
    )
    assert_success(result)
    payload = parse_json_line(result.stdout)
    failures = [entry for entry in payload["depths"] if entry["error"]]
    print_live_metric("e02.summary", iterations=iterations, depths=payload["depths"])
    assert not failures

    if full_experiment_enabled():
        depth_100 = next(entry for entry in payload["depths"] if entry["depth"] == 100)
        assert depth_100["mount_p99_ms"] < 5
