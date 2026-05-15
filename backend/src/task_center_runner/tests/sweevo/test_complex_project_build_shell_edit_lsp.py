"""Live regression for the mixed shell-edit + LSP project-build scenario."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox.api import ReadFileRequest, SandboxCaller

from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.scenarios.sandbox._metrics import PERF_SCHEMA
from task_center_runner.squad.complex_project_build_shell_edit_lsp_probe import (
    METRICS_PATH,
    WORKSPACE_ROOT,
)
from task_center_runner.stores import TaskCenterStoreBundle
from task_center_runner.sweevo_adapter import run_sweevo_scenario


pytestmark = pytest.mark.asyncio

_LSP_NAMES = (
    "lsp.hover",
    "lsp.find_definitions",
    "lsp.find_references",
    "lsp.query_symbols",
    "lsp.diagnostics",
)


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - task_center_runner requires PostgreSQL",
)
@pytest.mark.timeout(1200)
async def test_complex_project_build_shell_edit_lsp_smoke(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.complex_project_build_shell_edit_lsp_smoke"]
    scenario = scenario_cls()
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )
    await _assert_shell_edit_lsp_contract(
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
    await _assert_shell_edit_lsp_contract(
        report=report,
        sandbox_id=sandbox_id,
        smoke=False,
    )


async def _assert_shell_edit_lsp_contract(
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

    caller = SandboxCaller(agent_id="complex-project-build-shell-edit-lsp-test")
    perf = await _read_json(sandbox_id, METRICS_PATH, caller)
    summary = await _read_json(
        sandbox_id,
        f"{WORKSPACE_ROOT}/.metrics/summary.json",
        caller,
    )

    assert perf.get("schema") == PERF_SCHEMA
    for top_key in (
        "tool_use",
        "layer_stack",
        "overlay",
        "occ",
        "phases",
        "shell_edit",
        "lsp_correctness",
    ):
        assert top_key in perf, f"perf.json missing {top_key!r}"

    assert int(summary["pytest_exit_code"]) == 0
    routing = summary["edit_routing"]
    logical_edit_count = int(routing["logical_edit_count"])
    shell_edit_count = int(routing["shell_edit_count"])
    edit_file_count = int(routing["edit_file_edit_count"])
    logical_floor = 90 if smoke else 600
    tolerance = 0.05 if smoke else 0.03
    assert logical_edit_count >= logical_floor
    assert shell_edit_count >= math.floor(logical_edit_count / 3)
    assert edit_file_count >= math.floor(logical_edit_count / 3)
    assert abs(float(routing["shell_edit_ratio"]) - (1 / 3)) <= tolerance
    assert routing["routing_rule"] == "logical_edit_index % 3 == 2"

    lsp_correctness = summary["lsp_correctness"]
    lsp_floor = 5 if smoke else 40
    total_floor = 25 if smoke else 200
    assert int(lsp_correctness["total_checks"]) >= total_floor
    assert int(lsp_correctness["failed_checks"]) == 0
    assert int(lsp_correctness["passed_checks"]) == int(
        lsp_correctness["total_checks"]
    )
    by_tool = lsp_correctness["by_tool"]
    weak = {name: by_tool.get(name, 0) for name in _LSP_NAMES if by_tool.get(name, 0) < lsp_floor}
    assert weak == {}

    diagnostic_probe = summary["diagnostic_probe"]
    assert diagnostic_probe["error_detected"] is True
    assert diagnostic_probe["repair_cleared"] is True
    assert int(diagnostic_probe["diagnostic_checks"]) >= (4 if smoke else 10)

    shell_edit = summary["shell_edit"]
    assert int(shell_edit["count"]) == shell_edit_count
    assert int(shell_edit["errors"]) == 0
    assert int(shell_edit["overlay_capture_count"]) >= shell_edit_count
    assert int(shell_edit["changed_paths_total"]) >= shell_edit_count
    assert perf["shell_edit"]["count"] == shell_edit["count"]

    tri_source_checks = [
        check for check in report.sandbox_checks if check.name.startswith("projection.tri_source.")
    ]
    assert tri_source_checks, "tri-source projection checks missing"
    assert all(check.passed for check in tri_source_checks)

    junit = await _read_text(
        sandbox_id,
        f"{WORKSPACE_ROOT}/.metrics/pytest.xml",
        caller,
    )
    junit_root = ElementTree.fromstring(junit)
    junit_suite = junit_root
    if junit_root.tag != "testsuite":
        junit_suite = junit_root.find("testsuite")
        if junit_suite is None:
            junit_suite = junit_root
    assert int(junit_suite.get("failures", "0")) == 0
    assert int(junit_suite.get("errors", "0")) == 0


async def _read_json(
    sandbox_id: str,
    path: str,
    caller: SandboxCaller,
) -> dict[str, Any]:
    return json.loads(await _read_text(sandbox_id, path, caller))


async def _read_text(
    sandbox_id: str,
    path: str,
    caller: SandboxCaller,
) -> str:
    read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=path, caller=caller),
    )
    assert read.success and read.exists, f"missing sandbox file: {path}"
    return read.content
