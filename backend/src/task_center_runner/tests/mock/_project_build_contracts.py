"""Shared assertions for project-build SWE-EVO scenario tests."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import sandbox.api as sandbox_api
from sandbox.api import ReadFileRequest, SandboxCaller, ShellRequest
from sandbox.occ.service import AUTO_SQUASH_MAX_DEPTH

from task_center_runner.agent.mock.complex_project_build_grep_glob_probe import (
    METRICS_PATH as GREP_GLOB_METRICS_PATH,
    SUMMARY_PATH as GREP_GLOB_SUMMARY_PATH,
)
from task_center_runner.agent.mock.complex_project_build_probe import (
    METRICS_PATH as BUILD_METRICS_PATH,
    WORKSPACE_ROOT,
)
from task_center_runner.agent.mock.complex_project_build_shell_edit_lsp_probe import (
    METRICS_PATH as SHELL_EDIT_LSP_METRICS_PATH,
)
from task_center_runner.audit.events import EventType
from task_center_runner.core.runner import RunReport
from task_center_runner.scenarios.sandbox._metrics import PERF_SCHEMA


_LSP_NAMES = (
    "lsp.hover",
    "lsp.find_definitions",
    "lsp.find_references",
    "lsp.query_symbols",
    "lsp.diagnostics",
)

_SUBMISSION_TOOL_NAMES = {
    "submit_execution_handoff",
    "submit_plan_closes_goal",
    "submit_plan_defers_goal",
    "submit_execution_success",
    "submit_execution_blocker",
    "submit_evaluation_success",
    "submit_evaluation_failure",
    "submit_verification_success",
    "submit_verification_failure",
}


@dataclass(frozen=True, slots=True)
class ComplexBuildContract:
    tool_call_floor: int
    required_sandbox_events: tuple[EventType, ...]
    require_squash_events: bool
    lsp_floor: int
    api_read_floor: int
    api_edit_floor: int
    api_shell_floor: int
    require_layer_squash_metrics: bool
    junit_test_floor: int


@dataclass(frozen=True, slots=True)
class ShellEditLspContract:
    logical_edit_floor: int
    shell_ratio_tolerance: float
    lsp_floor: int
    total_lsp_floor: int
    diagnostic_checks_floor: int


@dataclass(frozen=True, slots=True)
class GrepGlobContract:
    scenario_name: str
    tool_call_floor: int
    grep_floor: int
    glob_floor: int
    edit_floor: int


_COMPLEX_BUILD_SMOKE = ComplexBuildContract(
    tool_call_floor=250,
    required_sandbox_events=(
        EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
        EventType.SANDBOX_OCC_CHANGES_COMMITTED,
        EventType.SANDBOX_CONFLICT_DETECTED,
    ),
    require_squash_events=False,
    lsp_floor=3,
    api_read_floor=5,
    api_edit_floor=1,
    api_shell_floor=1,
    require_layer_squash_metrics=False,
    junit_test_floor=5,
)

_COMPLEX_BUILD_FULL = ComplexBuildContract(
    tool_call_floor=2000,
    required_sandbox_events=(
        EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED,
        EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
        EventType.SANDBOX_OCC_CHANGES_COMMITTED,
        EventType.SANDBOX_CONFLICT_DETECTED,
    ),
    require_squash_events=True,
    lsp_floor=30,
    api_read_floor=40,
    api_edit_floor=10,
    api_shell_floor=3,
    require_layer_squash_metrics=True,
    junit_test_floor=30,
)

_SHELL_EDIT_LSP_SMOKE = ShellEditLspContract(
    logical_edit_floor=90,
    shell_ratio_tolerance=0.05,
    lsp_floor=5,
    total_lsp_floor=25,
    diagnostic_checks_floor=4,
)

_SHELL_EDIT_LSP_FULL = ShellEditLspContract(
    logical_edit_floor=600,
    shell_ratio_tolerance=0.03,
    lsp_floor=40,
    total_lsp_floor=200,
    diagnostic_checks_floor=10,
)

_GREP_GLOB_SMOKE = GrepGlobContract(
    scenario_name="sandbox.complex_project_build_grep_glob_smoke",
    tool_call_floor=250,
    grep_floor=40,
    glob_floor=20,
    edit_floor=30,
)

_GREP_GLOB_FULL = GrepGlobContract(
    scenario_name="sandbox.complex_project_build_grep_glob",
    tool_call_floor=2000,
    grep_floor=300,
    glob_floor=100,
    edit_floor=300,
)


async def assert_complex_build_smoke_contract(
    *,
    report: RunReport,
    sandbox_id: str,
) -> None:
    await _assert_complex_build_contract(
        report=report,
        sandbox_id=sandbox_id,
        contract=_COMPLEX_BUILD_SMOKE,
    )


async def assert_complex_build_full_contract(
    *,
    report: RunReport,
    sandbox_id: str,
) -> None:
    await _assert_complex_build_contract(
        report=report,
        sandbox_id=sandbox_id,
        contract=_COMPLEX_BUILD_FULL,
    )


async def assert_shell_edit_lsp_smoke_contract(
    *,
    report: RunReport,
    sandbox_id: str,
) -> None:
    await _assert_shell_edit_lsp_contract(
        report=report,
        sandbox_id=sandbox_id,
        contract=_SHELL_EDIT_LSP_SMOKE,
    )


async def assert_shell_edit_lsp_full_contract(
    *,
    report: RunReport,
    sandbox_id: str,
) -> None:
    await _assert_shell_edit_lsp_contract(
        report=report,
        sandbox_id=sandbox_id,
        contract=_SHELL_EDIT_LSP_FULL,
    )


async def assert_grep_glob_smoke_contract(
    *,
    report: RunReport,
    sandbox_id: str,
) -> None:
    await _assert_grep_glob_contract(
        report=report,
        sandbox_id=sandbox_id,
        contract=_GREP_GLOB_SMOKE,
    )


async def assert_grep_glob_full_contract(
    *,
    report: RunReport,
    sandbox_id: str,
) -> None:
    await _assert_grep_glob_contract(
        report=report,
        sandbox_id=sandbox_id,
        contract=_GREP_GLOB_FULL,
    )


async def _assert_complex_build_contract(
    *,
    report: RunReport,
    sandbox_id: str,
    contract: ComplexBuildContract,
) -> None:
    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    assert len(report.tool_calls) >= contract.tool_call_floor, (
        f"tool_calls={len(report.tool_calls)} below floor {contract.tool_call_floor}"
    )

    seen_events = {event.type for event in report.events}
    missing_events = sorted(
        event.value for event in contract.required_sandbox_events if event not in seen_events
    )
    assert not missing_events, f"missing in-memory events: {missing_events}"

    sandbox_log = report.run_dir / "sandbox_events.jsonl"
    assert sandbox_log.exists()
    logged_events = {
        EventType(row["event_type"])
        for row in _jsonl_rows(sandbox_log)
    }
    missing_logged = sorted(
        event.value
        for event in contract.required_sandbox_events
        if event not in logged_events
    )
    assert not missing_logged, f"missing persisted events: {missing_logged}"

    if contract.require_squash_events:
        squash_event_count = sum(
            1
            for event in report.events
            if event.type == EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED
        )
        assert squash_event_count >= 10, (
            f"only {squash_event_count} squash events; expected >= 10"
        )

    edit_count = sum(1 for c in report.tool_calls if c.tool_name == "edit_file")
    write_count = sum(1 for c in report.tool_calls if c.tool_name == "write_file")
    if write_count == 0:
        assert edit_count > 0
    else:
        ratio = edit_count / write_count
        assert ratio >= 4.0, (
            f"edit:write={ratio:.2f} (edit={edit_count}, write={write_count}) below 4.0"
        )

    lsp_counts = {
        name: sum(1 for c in report.tool_calls if c.tool_name == name)
        for name in _LSP_NAMES
    }
    weak = [name for name, cnt in lsp_counts.items() if cnt < contract.lsp_floor]
    assert not weak, f"LSP tools below floor {contract.lsp_floor}: {lsp_counts}"

    caller = SandboxCaller(agent_id="complex-project-build-test")
    perf = await _read_json(sandbox_id, BUILD_METRICS_PATH, caller)
    assert perf.get("schema") == PERF_SCHEMA
    for top_key in ("tool_use", "layer_stack", "overlay", "occ", "phases"):
        assert top_key in perf, f"perf.json missing {top_key!r}: keys={list(perf.keys())}"

    probe_tool_calls = [
        c for c in report.tool_calls if c.tool_name not in _SUBMISSION_TOOL_NAMES
    ]
    perf_total_calls = int(perf["tool_use"].get("total_calls") or 0)
    assert abs(perf_total_calls - len(probe_tool_calls)) <= 5, (
        f"perf.tool_use.total_calls={perf_total_calls} vs "
        f"probe-toolkit-calls={len(probe_tool_calls)} "
        f"(report.tool_calls={len(report.tool_calls)})"
    )

    overlay = perf.get("overlay") or {}
    assert int(overlay.get("shell_calls") or 0) > 0, "overlay.shell_calls = 0"

    occ_committed_events = sum(
        1
        for row in _jsonl_rows(sandbox_log)
        if row.get("event_type") == EventType.SANDBOX_OCC_CHANGES_COMMITTED.value
    )
    perf_commit_count = int(perf["occ"].get("commit_count") or 0)
    assert occ_committed_events >= perf_commit_count, (
        f"occ.commit_count={perf_commit_count} > "
        f"SANDBOX_OCC_CHANGES_COMMITTED events={occ_committed_events}"
    )
    assert perf_commit_count > 0, "perf.occ.commit_count = 0"

    if contract.require_layer_squash_metrics:
        layer = perf.get("layer_stack") or {}
        assert int(layer.get("squash_count") or 0) >= 10
        assert float(layer.get("max_depth_before") or 0.0) > float(
            AUTO_SQUASH_MAX_DEPTH
        )

    summary = await _read_json(
        sandbox_id,
        f"{WORKSPACE_ROOT}/.metrics/summary.json",
        caller,
    )
    api_calls = summary.get("api_calls") or {}
    api_read_count = int(api_calls.get("read") or 0)
    api_edit_count = int(api_calls.get("edit") or 0)
    api_shell_count = int(api_calls.get("shell") or 0)
    assert api_read_count >= contract.api_read_floor, (
        f"api.read_file count={api_read_count}"
    )
    assert api_edit_count >= contract.api_edit_floor, (
        f"api.edit_file count={api_edit_count}"
    )
    assert api_shell_count >= contract.api_shell_floor, (
        f"api.shell count={api_shell_count}"
    )

    junit = await _read_text(
        sandbox_id,
        f"{WORKSPACE_ROOT}/.metrics/pytest.xml",
        caller,
    )
    junit_suite = _junit_suite(junit)
    failures = int(junit_suite.get("failures", "0"))
    errors = int(junit_suite.get("errors", "0"))
    tests = int(junit_suite.get("tests", "0"))
    assert failures == 0, f"pytest junit failures={failures}"
    assert errors == 0, f"pytest junit errors={errors}"
    assert tests >= contract.junit_test_floor, (
        f"pytest junit tests={tests} (floor {contract.junit_test_floor})"
    )

    shell_result = await sandbox_api.shell(
        sandbox_id,
        ShellRequest(
            command=f"test -s {WORKSPACE_ROOT}/.metrics/pytest.xml && echo OK",
            cwd=WORKSPACE_ROOT,
            timeout=60,
            caller=caller,
            description="complex_project_build pytest.xml readback",
        ),
    )
    assert shell_result.success
    assert shell_result.exit_code == 0
    assert "OK" in shell_result.stdout


async def _assert_shell_edit_lsp_contract(
    *,
    report: RunReport,
    sandbox_id: str,
    contract: ShellEditLspContract,
) -> None:
    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    caller = SandboxCaller(agent_id="complex-project-build-shell-edit-lsp-test")
    perf = await _read_json(sandbox_id, SHELL_EDIT_LSP_METRICS_PATH, caller)
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
    assert logical_edit_count >= contract.logical_edit_floor
    assert shell_edit_count >= math.floor(logical_edit_count / 3)
    assert edit_file_count >= math.floor(logical_edit_count / 3)
    assert abs(float(routing["shell_edit_ratio"]) - (1 / 3)) <= (
        contract.shell_ratio_tolerance
    )
    assert routing["routing_rule"] == "logical_edit_index % 3 == 2"

    lsp_correctness = summary["lsp_correctness"]
    assert int(lsp_correctness["total_checks"]) >= contract.total_lsp_floor
    assert int(lsp_correctness["failed_checks"]) == 0
    assert int(lsp_correctness["passed_checks"]) == int(
        lsp_correctness["total_checks"]
    )
    by_tool = lsp_correctness["by_tool"]
    weak = {
        name: by_tool.get(name, 0)
        for name in _LSP_NAMES
        if by_tool.get(name, 0) < contract.lsp_floor
    }
    assert weak == {}

    diagnostic_probe = summary["diagnostic_probe"]
    assert diagnostic_probe["error_detected"] is True
    assert diagnostic_probe["repair_cleared"] is True
    assert int(diagnostic_probe["diagnostic_checks"]) >= (
        contract.diagnostic_checks_floor
    )

    shell_edit = summary["shell_edit"]
    assert int(shell_edit["count"]) == shell_edit_count
    assert int(shell_edit["errors"]) == 0
    assert int(shell_edit["overlay_capture_count"]) >= shell_edit_count
    assert int(shell_edit["changed_paths_total"]) >= shell_edit_count
    assert perf["shell_edit"]["count"] == shell_edit["count"]

    tri_source_checks = [
        check for check in report.sandbox_checks
        if check.name.startswith("projection.tri_source.")
    ]
    assert tri_source_checks, "tri-source projection checks missing"
    assert all(check.passed for check in tri_source_checks)

    junit = await _read_text(
        sandbox_id,
        f"{WORKSPACE_ROOT}/.metrics/pytest.xml",
        caller,
    )
    junit_suite = _junit_suite(junit)
    assert int(junit_suite.get("failures", "0")) == 0
    assert int(junit_suite.get("errors", "0")) == 0


async def _assert_grep_glob_contract(
    *,
    report: RunReport,
    sandbox_id: str,
    contract: GrepGlobContract,
) -> None:
    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    tool_counts = {
        name: sum(1 for call in report.tool_calls if call.tool_name == name)
        for name in ("grep", "glob", "edit_file")
    }
    assert len(report.tool_calls) >= contract.tool_call_floor, (
        f"tool_calls={len(report.tool_calls)} below floor {contract.tool_call_floor}"
    )
    assert tool_counts["grep"] >= contract.grep_floor, tool_counts
    assert tool_counts["glob"] >= contract.glob_floor, tool_counts
    assert tool_counts["edit_file"] >= contract.edit_floor, tool_counts

    caller = SandboxCaller(agent_id="complex-project-build-grep-glob-test")
    perf = await _read_json(sandbox_id, GREP_GLOB_METRICS_PATH, caller)
    summary = await _read_json(sandbox_id, GREP_GLOB_SUMMARY_PATH, caller)

    assert perf.get("schema") == PERF_SCHEMA
    assert perf.get("scenario") == contract.scenario_name
    assert int(summary["pytest_exit_code"]) == 0

    grep_glob = summary["grep_glob"]
    assert int(grep_glob["search_failures"]) == 0
    assert int(grep_glob["grep_count"]) >= contract.grep_floor
    assert int(grep_glob["glob_count"]) >= contract.glob_floor
    assert int(grep_glob["search_checks"]) >= contract.grep_floor
    assert int(grep_glob["negative_grep_checks"]) > 0
    assert {"files_with_matches", "count", "content"}.issubset(
        set(grep_glob["grep_modes"])
    )
    assert perf["grep_glob"]["grep_count"] == grep_glob["grep_count"]
    assert perf["grep_glob"]["glob_count"] == grep_glob["glob_count"]
    assert int(summary["tool_use"]["toolkit_total"]) >= contract.tool_call_floor


def _junit_suite(junit: str) -> ElementTree.Element:
    root = ElementTree.fromstring(junit)
    if root.tag == "testsuite":
        return root
    suite = root.find("testsuite")
    return suite if suite is not None else root


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


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
