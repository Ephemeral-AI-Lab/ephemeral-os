"""Live regression for the grep + glob workflow project-build scenario."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox.api import ReadFileRequest, SandboxCaller

from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.scenarios.sandbox._metrics import PERF_SCHEMA
from task_center_runner.agent.mock.complex_project_build_grep_glob_probe import (
    METRICS_PATH,
    SUMMARY_PATH,
)
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


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - task_center_runner requires PostgreSQL",
)
@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_RUN_HEAVY_LIVE_E2E"),
    reason="heavy live e2e - opt-in via EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1",
)
@pytest.mark.timeout(3600)
async def test_complex_project_build_grep_glob_full(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.complex_project_build_grep_glob"]
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
        smoke=False,
    )


async def _assert_grep_glob_contract(
    *,
    report,
    sandbox_id: str,
    smoke: bool,
) -> None:
    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    tool_floor = 250 if smoke else 2000
    grep_floor = 40 if smoke else 300
    glob_floor = 20 if smoke else 100
    edit_floor = 30 if smoke else 300

    tool_counts = {
        name: sum(1 for call in report.tool_calls if call.tool_name == name)
        for name in ("grep", "glob", "edit_file")
    }
    assert len(report.tool_calls) >= tool_floor, (
        f"tool_calls={len(report.tool_calls)} below floor {tool_floor}"
    )
    assert tool_counts["grep"] >= grep_floor, tool_counts
    assert tool_counts["glob"] >= glob_floor, tool_counts
    assert tool_counts["edit_file"] >= edit_floor, tool_counts

    caller = SandboxCaller(agent_id="complex-project-build-grep-glob-test")
    perf = await _read_json(sandbox_id, METRICS_PATH, caller)
    summary = await _read_json(sandbox_id, SUMMARY_PATH, caller)

    assert perf.get("schema") == PERF_SCHEMA
    assert perf.get("scenario") == (
        "sandbox.complex_project_build_grep_glob_smoke"
        if smoke
        else "sandbox.complex_project_build_grep_glob"
    )
    assert int(summary["pytest_exit_code"]) == 0

    grep_glob = summary["grep_glob"]
    assert int(grep_glob["search_failures"]) == 0
    assert int(grep_glob["grep_count"]) >= grep_floor
    assert int(grep_glob["glob_count"]) >= glob_floor
    assert int(grep_glob["search_checks"]) >= grep_floor
    assert int(grep_glob["negative_grep_checks"]) > 0
    assert {"files_with_matches", "count", "content"}.issubset(
        set(grep_glob["grep_modes"])
    )
    assert perf["grep_glob"]["grep_count"] == grep_glob["grep_count"]
    assert perf["grep_glob"]["glob_count"] == grep_glob["glob_count"]
    assert int(summary["tool_use"]["toolkit_total"]) >= tool_floor


async def _read_json(
    sandbox_id: str,
    path: str,
    caller: SandboxCaller,
) -> dict[str, Any]:
    read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=path, caller=caller),
    )
    assert read.success and read.exists, f"missing sandbox file: {path}"
    return json.loads(read.content)
