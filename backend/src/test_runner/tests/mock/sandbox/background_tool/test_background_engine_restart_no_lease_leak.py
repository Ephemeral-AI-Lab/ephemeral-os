"""3.4.4 engine-abandon cleanup and foreground recovery live regression."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_runner.benchmarks.sweevo.models import SWEEvoInstance
from test_runner.agent.mock.background_shell_probe import (
    ENGINE_RESTART_SUMMARY,
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


@pytest.mark.timeout(720)
async def test_background_engine_restart_no_lease_leak(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskStoreBundle,
) -> None:
    report, summary = await run_background_shell_scenario(
        scenario_name="sandbox.background_engine_restart_no_lease_leak",
        summary_path=ENGINE_RESTART_SUMMARY,
        sweevo_image_instance=sweevo_image_instance,
        workspace=workspace,
        audit_dir=audit_dir,
        stores=stores,
    )

    assert summary["mode"] == "engine_restart_no_lease_leak", summary
    assert summary["command_sessions_during_launch"] >= 1, summary
    assert summary["abandoned"]["is_error"] or summary["abandoned"]["cancelled"], summary
    assert not summary["abandoned_published"], summary
    assert summary["command_sessions_after"] == 0, summary
    assert not summary["foreground_shell"]["is_error"], summary
    assert not summary["recovery_write"]["is_error"], summary
    assert "recovery-ok" in summary["recovery_read_content"], summary

    assert_background_performance_artifacts(report)
