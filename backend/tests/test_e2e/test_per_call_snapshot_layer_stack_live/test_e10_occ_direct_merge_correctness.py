from __future__ import annotations

import asyncio

import pytest

from .conftest import (
    assert_success,
    barrier_overlay,
    make_workdir,
    print_live_metric,
    run_live_command,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e10_api_shell_same_file_conflict_has_single_winner(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e10")
    target = f"{workdir}/same.txt"
    commands = [
        f"sleep 0.1; printf 'writer_a\\n' > {target}; cat {target}",
        f"printf 'writer_b\\n' > {target}; cat {target}",
    ]
    with barrier_overlay(live_snapshot_sandbox, parties=len(commands)):
        results = await asyncio.gather(
            *[
                run_live_command(
                    live_snapshot_sandbox,
                    command,
                    timeout=30,
                    label=f"e10.api_same_file.{index}",
                )
                for index, command in enumerate(commands)
            ]
        )
    successes = [result for result in results if result.success]
    conflicts = [result for result in results if not result.success]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status == "aborted_version"
    final = await run_live_command(
        live_snapshot_sandbox,
        f"cat {target}",
        timeout=30,
        label="e10.api_same_file.final",
    )
    assert_success(final)
    final_value = final.stdout.strip().splitlines()[-1]
    print_live_metric(
        "e10.api_same_file",
        final=final_value,
        conflict_reason=conflicts[0].conflict_reason,
    )
    assert final_value in {"writer_a", "writer_b"}
