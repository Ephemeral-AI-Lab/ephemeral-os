"""T3 — Interleave live regression for ``shell(background=True)``.

One long-running background shell + 5 interleaved foreground shells.
Records foreground p95 mount latency to characterize the background
lease's effect on foreground p95. AC-3 expects foreground mount latency
to stay essentially unchanged from the no-background baseline.
"""

from __future__ import annotations

import pytest

from benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.agent.mock.background_shell_probe import (
    run_background_shell_interleave_probe,
    seed_workspace,
)
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(420)
async def test_background_shell_interleave(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
) -> None:
    sandbox_id = str(workspace["sandbox_id"])
    await seed_workspace(sandbox_id)
    summary = await run_background_shell_interleave_probe(
        sandbox_id=sandbox_id,
        foreground_count=5,
        background_sleep_s=30,
    )
    assert summary.mode == "interleave"
    assert len(summary.foreground_mount_s) == 5

    # AC-3: foreground p95 mount latency stays under 5 s even while a
    # background lease is held. The threshold is conservative to absorb
    # SWE-EVO warm-cache jitter; tighten once a per-instance baseline is
    # recorded in .sweevo_runs/scenario_logs.
    assert summary.foreground_p95_mount_s < 5.0, (
        f"AC-3 violation: foreground p95 mount_s "
        f"{summary.foreground_p95_mount_s:.3f}s exceeds 5 s budget"
    )

    # The background launch must complete (or be cancelled cleanly on
    # teardown) — never error out.
    assert len(summary.launches) == 1
    record = summary.launches[0]
    assert record.error is None, record
