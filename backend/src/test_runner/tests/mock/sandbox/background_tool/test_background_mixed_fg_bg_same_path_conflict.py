"""3.4.1 foreground write wins over same-path background command write."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_runner.benchmarks.sweevo.models import SWEEvoInstance
from test_runner.agent.mock.background_shell_probe import (
    MIXED_CONFLICT_SUMMARY,
)
from test_runner.core.stores import TaskStoreBundle
from test_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from test_runner.tests.mock.sandbox.background_tool._background_shell_invariants import (
    assert_background_performance_artifacts,
    run_background_shell_scenario,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not database_configured(), reason="database URL not configured"),
    pytest.mark.skipif(
        not live_e2e_heavy_enabled(),
        reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
    ),
]


@pytest.mark.timeout(600)
async def test_background_mixed_fg_bg_same_path_conflict(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskStoreBundle,
) -> None:
    report, summary = await run_background_shell_scenario(
        scenario_name="sandbox.background_mixed_fg_bg_same_path_conflict",
        summary_path=MIXED_CONFLICT_SUMMARY,
        sweevo_image_instance=sweevo_image_instance,
        workspace=workspace,
        audit_dir=audit_dir,
        stores=stores,
    )

    assert summary["mode"] == "mixed_fg_bg_same_path_conflict", summary
    assert not summary["foreground"]["is_error"], summary
    assert not summary["background"]["is_error"], summary
    assert summary["foreground_won"], summary
    assert not summary["background_won"], summary
    assert summary["background"]["status"] == "ok", summary
    write_total_s = float(
        summary["foreground"]["metadata"]["timings"].get("api.write.total_s", 0.0)
    )
    assert write_total_s < 5.0, summary

    assert_background_performance_artifacts(report)
