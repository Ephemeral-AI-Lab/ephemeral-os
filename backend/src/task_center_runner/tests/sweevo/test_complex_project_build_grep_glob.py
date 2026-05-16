"""Live regression for the grep + glob workflow project-build scenario."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchmarks.sweevo.models import SWEEvoInstance

from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.benchmarks.sweevo.fixtures import run_sweevo_scenario


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - task_center_runner requires PostgreSQL",
)
@pytest.mark.timeout(1200)
async def test_complex_project_build_grep_glob_smoke(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.complex_project_build_grep_glob_smoke"]
    scenario = scenario_cls()
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )
    await _assert_grep_glob_contract(
        report=report,
        sandbox_id=sandbox_id,
        smoke=True,
    )


async def _assert_grep_glob_contract(
    *,
    report,
    sandbox_id: str,
    smoke: bool,
) -> None:
    del sandbox_id, smoke
    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]
