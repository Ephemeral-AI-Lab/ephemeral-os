"""3.5.6 LSP service survives peer publishes and restarts after eviction."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_runner.benchmarks.sweevo.models import SWEEvoInstance
from test_runner.agent.mock.plugin_workspace_probe import SERVICE_EVICT_SUMMARY
from test_runner.core.stores import TaskStoreBundle
from test_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from test_runner.tests.mock.sandbox.plugin._plugin_invariants import (
    assert_no_internal_sandbox_errors,
    assert_plugin_o1_artifacts,
    run_plugin_scenario,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not database_configured(), reason="database URL not configured"),
    pytest.mark.skipif(
        not live_e2e_heavy_enabled(),
        reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
    ),
]


@pytest.mark.timeout(900)
async def test_plugin_service_survives_peer_publish_and_evict(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskStoreBundle,
) -> None:
    report, summary = await run_plugin_scenario(
        scenario_name="sandbox.plugin_service_evict",
        summary_path=SERVICE_EVICT_SUMMARY,
        sweevo_image_instance=sweevo_image_instance,
        workspace=workspace,
        audit_dir=audit_dir,
        stores=stores,
    )

    assert summary["schema"] == "test_runner.plugin_workspace.v1"
    assert summary["mode"] == "service_evict"
    assert summary["peer_publish_count"] == 5
    assert summary["refresh_start_delta"] == 0.0
    assert summary["refresh_total"] >= 1.0
    assert summary["refresh_service_state"] == "ready"
    assert summary["refresh_service_refresh_count"] >= 1.0
    assert summary["refresh_service_health_ok"] is True
    assert summary["refresh_lsp_ms"] > 0.0
    assert summary["post_refresh_warm_lsp_ms"] > 0.0
    assert summary["evict_ensure"]["success"] is True
    assert summary["evict_ensure"]["already_loaded"] is False
    assert summary["evict_ensure"]["service_processes_started"] is True
    assert summary["evict_ensure"]["started_service_process_count"] >= 1
    assert summary["post_evict_call_start_delta"] >= 1.0
    assert summary["post_evict_call_start_total"] >= 1.0
    assert summary["post_evict_call_lsp_ms"] > 0.0
    assert summary["warm_lsp_p95_ms"] <= 500.0

    assert_plugin_o1_artifacts(report)
    assert_no_internal_sandbox_errors(report.run_dir)
