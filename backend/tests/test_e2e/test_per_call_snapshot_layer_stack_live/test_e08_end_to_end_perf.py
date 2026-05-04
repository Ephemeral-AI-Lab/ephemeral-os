from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    full_experiment_enabled,
    p95_ms,
    print_live_metric,
    run_live_commands,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e08_api_shell_wall_time_baseline(live_snapshot_sandbox):
    total = 100 if full_experiment_enabled() else 20
    results = await run_live_commands(
        live_snapshot_sandbox,
        [
            f"python3 - <<'PY'\nprint({index} * {index})\nPY"
            for index in range(total)
        ],
        timeout=60,
        label="e08.shell_baseline",
        labels=[f"e08.shell_baseline.{index}" for index in range(total)],
    )
    failures = [result for result in results if not result.success]
    for result in results:
        assert_success(result)
    print_live_metric(
        "e08.summary",
        total=total,
        failures=len(failures),
        p95_ms=round(p95_ms(result.elapsed_ms for result in results), 2),
    )
    assert not failures
