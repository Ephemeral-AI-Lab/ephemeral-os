"""Full live regression for the mixed shell-edit + LSP project-build scenario."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.benchmarks.sweevo.fixtures import run_sweevo_scenario
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.tests.sweevo._project_build_contracts import (
    assert_shell_edit_lsp_full_contract,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - task_center_runner requires PostgreSQL",
)
@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_RUN_HEAVY_LIVE_E2E"),
    reason="heavy live e2e - opt-in via EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1",
)
@pytest.mark.timeout(3600)
async def test_complex_project_build_shell_edit_lsp_full(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.complex_project_build_shell_edit_lsp"]
    scenario = scenario_cls()
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )
    await assert_shell_edit_lsp_full_contract(
        report=report,
        sandbox_id=sandbox_id,
    )
