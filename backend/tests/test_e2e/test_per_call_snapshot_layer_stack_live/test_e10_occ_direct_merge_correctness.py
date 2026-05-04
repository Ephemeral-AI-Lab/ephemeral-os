from __future__ import annotations

import asyncio

import pytest

from .conftest import (
    assert_success,
    make_workdir,
    print_live_metric,
    run_live_command,
    xfail_production_binding_missing,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e10_raw_shell_same_file_is_not_occ_guarded(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e10")
    target = f"{workdir}/same.txt"
    commands = [
        f"sleep 0.1; printf 'writer_a\\n' > {target}; cat {target}",
        f"printf 'writer_b\\n' > {target}; cat {target}",
    ]
    results = await asyncio.gather(
        *[
            run_live_command(
                live_snapshot_sandbox,
                command,
                timeout=30,
                label=f"e10.raw_same_file.{index}",
            )
            for index, command in enumerate(commands)
        ]
    )
    for result in results:
        assert_success(result)
    final = await run_live_command(
        live_snapshot_sandbox,
        f"cat {target}",
        timeout=30,
        label="e10.raw_same_file.final",
    )
    assert_success(final)
    final_value = final.stdout.strip().splitlines()[-1]
    print_live_metric("e10.raw_same_file", final=final_value)
    assert final_value in {"writer_a", "writer_b"}


async def test_e10_production_occ_and_direct_merge_contract_required():
    xfail_production_binding_missing("E10 OCC and direct-merge correctness")
