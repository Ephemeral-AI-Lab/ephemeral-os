from __future__ import annotations

import asyncio

import pytest

from .conftest import (
    assert_success,
    make_workdir,
    run_live_command,
    xfail_production_binding_missing,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e06_long_shell_survives_unrelated_file_churn(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e06")
    seed = await run_live_command(
        live_snapshot_sandbox,
        f"printf 'stable\\n' > {workdir}/stable.txt",
        timeout=30,
        label="e06.seed",
    )
    assert_success(seed)

    long_read = run_live_command(
        live_snapshot_sandbox,
        (
            "python3 - <<'PY'\n"
            "import pathlib, time\n"
            f"path = pathlib.Path({workdir!r}) / 'stable.txt'\n"
            "first = path.read_text()\n"
            "time.sleep(3)\n"
            "second = path.read_text()\n"
            "assert first == second == 'stable\\n'\n"
            "print(second.strip())\n"
            "PY"
        ),
        timeout=20,
        label="e06.long_read",
    )
    churn = run_live_command(
        live_snapshot_sandbox,
        (
            f"mkdir -p {workdir}/churn; "
            f"for i in $(seq 1 200); do printf \"$i\\n\" > {workdir}/churn/$i.txt; done; "
            f"rm -rf {workdir}/churn"
        ),
        timeout=20,
        label="e06.churn",
    )
    long_result, churn_result = await asyncio.gather(long_read, churn)
    assert_success(long_result)
    assert_success(churn_result)
    assert "stable" in long_result.stdout


async def test_e06_production_layer_gc_refcount_contract_required():
    xfail_production_binding_missing("E6 layer GC under contention")
