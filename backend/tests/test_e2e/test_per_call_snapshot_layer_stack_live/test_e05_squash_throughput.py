from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    full_experiment_enabled,
    make_workdir,
    parse_json_line,
    print_live_metric,
    python_json_command,
    run_live_command,
    xfail_production_binding_missing,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e05_live_append_rate_measurement(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e05")
    writes = 15_000 if full_experiment_enabled() else 500
    command = python_json_command(
        f"""
        import json
        import pathlib
        import time

        root = pathlib.Path({workdir!r})
        out = root / "layers"
        out.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        for index in range({writes}):
            (out / f"layer_{{index:05d}}.txt").write_text(f"{{index}}\\n", encoding="utf-8")
        elapsed = time.perf_counter() - start
        print(json.dumps({{"writes": {writes}, "elapsed_s": elapsed, "writes_per_s": {writes} / elapsed}}))
        """
    )
    result = await run_live_command(
        live_snapshot_sandbox,
        command,
        timeout=600 if full_experiment_enabled() else 120,
        label="e05.append_rate",
    )
    assert_success(result)
    payload = parse_json_line(result.stdout)
    print_live_metric("e05.summary", **payload)
    assert payload["writes"] == writes
    assert payload["writes_per_s"] > 50


async def test_e05_production_squash_worker_contract_required():
    xfail_production_binding_missing("E5 squash throughput vs append rate")
