from __future__ import annotations

import asyncio

import pytest

from .conftest import (
    assert_success,
    full_experiment_enabled,
    p95_ms,
    print_live_metric,
    run_live_command,
    xfail_production_binding_missing,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e08_live_shell_wall_time_baseline(live_snapshot_sandbox):
    total = 100 if full_experiment_enabled() else 20
    results = await asyncio.gather(
        *[
            run_live_command(
                live_snapshot_sandbox,
                f"python3 - <<'PY'\nprint({index} * {index})\nPY",
                timeout=60,
                label=f"e08.shell_baseline.{index}",
            )
            for index in range(total)
        ]
    )
    failures = [result for result in results if result.exit_code != 0]
    for result in results:
        assert_success(result)
    print_live_metric(
        "e08.summary",
        total=total,
        failures=len(failures),
        p95_ms=round(p95_ms(result.elapsed_ms for result in results), 2),
    )
    assert not failures


async def test_e08_old_vs_new_design_comparison_required():
    xfail_production_binding_missing("E8 end-to-end old-vs-new layer-stack comparison")
