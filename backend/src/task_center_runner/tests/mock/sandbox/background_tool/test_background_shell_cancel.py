"""T2 — Cancel-mid-flight live regression via the scenario harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox._shared.models import ReadFileRequest, SandboxCaller
from task_center_runner.agent.mock.background_shell_probe import CANCEL_SUMMARY
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.environments.sweevo_image.fixtures import (
    run_scenario_on_sweevo_image,
)
from task_center_runner.scenarios import SCENARIO_REGISTRY
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
async def test_background_shell_cancel(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.background_shell_cancel"]
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_scenario_on_sweevo_image(
        scenario_cls(),
        instance=sweevo_image_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )
    assert report.task_center_status == "done", report

    read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(
            path=CANCEL_SUMMARY,
            caller=SandboxCaller(agent_id="test.background_shell_cancel.read"),
        ),
    )
    assert read.success and read.exists, read
    summary = json.loads(read.content or "{}")
    assert summary["mode"] == "cancel", summary
    cancelled = [r for r in summary["launches"] if r["cancelled"]]
    assert len(cancelled) == summary["launch_count"], summary

    # AC-3: post-cancel foreground shell mount stays under 5 s.
    post_fg = summary["post_cancel_foreground"]
    assert not post_fg["is_error"], post_fg
    assert post_fg["duration_s"] < 5.0, (
        f"AC-3 violation: post-cancel foreground shell took "
        f"{post_fg['duration_s']:.3f}s (expected < 5 s)"
    )
