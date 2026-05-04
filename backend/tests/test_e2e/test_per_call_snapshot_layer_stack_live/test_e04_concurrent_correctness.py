from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    make_workdir,
    print_live_metric,
    run_live_command,
    run_live_commands,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e04_api_shell_concurrency_commits_disjoint_outputs(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e04")
    total = 60
    commands = [
        (
            f"mkdir -p {workdir}/out && "
            f"printf 'op={index}\\n' > {workdir}/out/op_{index}.txt && "
            f"cat {workdir}/out/op_{index}.txt"
        )
        for index in range(total)
    ]
    results = await run_live_commands(
        live_snapshot_sandbox,
        commands,
        timeout=60,
        label="e04.concurrent",
        labels=[f"e04.concurrent.{index}" for index in range(total)],
    )
    failures = [result for result in results if not result.success]
    verify = await run_live_command(
        live_snapshot_sandbox,
        f"find {workdir}/out -type f | wc -l",
        timeout=30,
        label="e04.verify_count",
    )
    assert_success(verify)
    print_live_metric(
        "e04.summary",
        total=total,
        failures=len(failures),
        avg_ms=sum(result.elapsed_ms for result in results) / len(results),
    )
    assert not failures
    assert int(verify.stdout.strip().splitlines()[-1]) == total
