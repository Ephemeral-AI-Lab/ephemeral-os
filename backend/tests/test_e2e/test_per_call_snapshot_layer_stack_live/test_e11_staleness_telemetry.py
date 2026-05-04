from __future__ import annotations

import asyncio

import pytest

from .conftest import (
    assert_success,
    make_workdir,
    print_live_metric,
    run_live_command,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e11_api_shell_accepts_unrelated_concurrent_updates(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e11")
    config = f"{workdir}/config.yaml"
    generated = f"{workdir}/generated/output.json"
    seed = await run_live_command(
        live_snapshot_sandbox,
        f"mkdir -p {workdir}/generated && printf 'version: 1\\n' > {config}",
        timeout=30,
        label="e11.seed",
    )
    assert_success(seed)
    long_shell = run_live_command(
        live_snapshot_sandbox,
        (
            f"version=$(cat {config}); sleep 2; "
            f"mkdir -p {workdir}/generated; "
            f"printf '{{\"derived_from\": %s}}\\n' \"$version\" > {generated}; cat {generated}"
        ),
        timeout=30,
        label="e11.long_shell",
    )
    concurrent = run_live_command(
        live_snapshot_sandbox,
        f"for i in $(seq 1 6); do printf \"$i\\n\" > {workdir}/advance_$i.txt; done",
        timeout=30,
        label="e11.concurrent_advances",
    )
    long_result, concurrent_result = await asyncio.gather(long_shell, concurrent)
    assert_success(long_result)
    assert_success(concurrent_result)
    print_live_metric(
        "e11.api_staleness",
        output=long_result.stdout.strip(),
        changed_paths=list(long_result.changed_paths),
    )
    assert generated in long_result.changed_paths
