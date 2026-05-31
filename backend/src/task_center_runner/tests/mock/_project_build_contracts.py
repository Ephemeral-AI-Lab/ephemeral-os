"""Shared assertions for project-build SWE-EVO scenario tests."""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
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
from task_center_runner.tests.mock._layer_stack_occ_overlay_assertions import (
    assert_o1_workspace_resource_snapshots,
    assert_resource_key_max,
    assert_timing_keys_present,
    load_performance_report,
    mapping,
)


_LSP_NAMES = (
    "lsp.hover",
    "lsp.find_definitions",
    "lsp.find_references",
    "lsp.query_symbols",
    "lsp.diagnostics",
)

_SUBMISSION_TOOL_NAMES = {
    "submit_workflow_handoff",
    "submit_planner_outcome",
    "submit_generator_outcome",
    "submit_reducer_outcome",
}
_NON_PROBE_TOOL_NAMES = _SUBMISSION_TOOL_NAMES | {"ask_advisor"}

_DIRECT_FILE_TOOLS = ("read_file", "write_file", "edit_file")
_PROJECT_BUILD_REQUIRED_TOOLS = (*_DIRECT_FILE_TOOLS, "shell")
_SEARCH_TOOLS = ("grep", "glob")
_PROJECT_BUILD_UPPERDIR_BUDGET_BYTES = 1_048_576
_WARM_SEARCH_P95_BUDGET_MS = 500.0
_WARM_LSP_NO_REFRESH_P95_BUDGET_MS = 500.0
_DIRECT_FILE_OVERLAY_RESOURCE_KEYS = (
    "resource.command_exec.workspace_tree_bytes",
    "resource.command_exec.workspace_tree_exists",
    "resource.command_exec.run_dir_tree_bytes",
    "resource.command_exec.run_dir_tree_exists",
    "resource.command_exec.upperdir_tree_bytes",
    "resource.command_exec.upperdir_tree_exists",
)
_SEARCH_PUBLISH_TIMING_PREFIXES = (
    "occ.",
    "layer_stack.publish.",
    "layer_stack.auto_squash.",
)


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


async def assert_project_build_full_o1_disk_budget(report: RunReport) -> None:
    perf = await _load_task_center_performance_report(report)
    _assert_no_internal_sandbox_errors(report.run_dir)
    _assert_project_build_per_tool_report(
        perf,
        required_tools=(*_PROJECT_BUILD_REQUIRED_TOOLS, *_LSP_NAMES),
    )
    _assert_workspace_tree_is_o1(report.run_dir, perf)
    _assert_direct_file_samples_do_not_create_overlay_resources(perf)
    _assert_upperdir_within_single_operation_budget(perf)
    _assert_manifest_depth_within_squash_target(perf)


async def assert_project_build_grep_glob_low_latency_after_many_edits(
    report: RunReport,
) -> None:
    perf = await _load_task_center_performance_report(report)
    _assert_no_internal_sandbox_errors(report.run_dir)
    _assert_project_build_per_tool_report(
        perf,
        required_tools=(
            *_PROJECT_BUILD_REQUIRED_TOOLS,
            *_SEARCH_TOOLS,
            "lsp.diagnostics",
            "lsp.find_references",
        ),
    )
    _assert_workspace_tree_is_o1(report.run_dir, perf)
    _assert_direct_file_samples_do_not_create_overlay_resources(perf)
    for tool_name in _SEARCH_TOOLS:
        p95_ms = _warm_tool_p95_ms(perf, tool_name)
        assert p95_ms <= _WARM_SEARCH_P95_BUDGET_MS, (
            f"{tool_name} warm p95 {p95_ms:.3f}ms exceeds {_WARM_SEARCH_P95_BUDGET_MS:.0f}ms"
        )
        _assert_tool_samples_lack_publish_timings(perf, tool_name)


async def assert_project_build_shell_edit_lsp_remount_not_restart(
    report: RunReport,
    *,
    sandbox_id: str,
) -> None:
    perf = await _load_task_center_performance_report(report)
    _assert_no_internal_sandbox_errors(report.run_dir)
    _assert_project_build_per_tool_report(
        perf,
        required_tools=(*_PROJECT_BUILD_REQUIRED_TOOLS, *_LSP_NAMES),
    )
    _assert_workspace_tree_is_o1(report.run_dir, perf)
    _assert_direct_file_samples_do_not_create_overlay_resources(perf)

    caller = SandboxCaller(agent_id="complex-project-build-shell-edit-lsp-perf-test")
    summary = await _read_json(
        sandbox_id,
        f"{WORKSPACE_ROOT}/.metrics/summary.json",
        caller,
    )
    assert summary["diagnostic_probe"]["error_detected"] is True
    assert summary["diagnostic_probe"]["repair_cleared"] is True
    assert int(summary["lsp_correctness"]["failed_checks"]) == 0
    assert int(summary["shell_edit"]["count"]) > 0

    lsp_samples = _tool_samples_by_prefix(perf, "lsp.")
    assert lsp_samples, "missing LSP samples"
    max_start_delta = _max_sample_timing(lsp_samples, "lsp.session.start_count_delta")
    max_remount_delta = _max_sample_timing(
        lsp_samples,
        "lsp.session.remount_count_delta",
    )
    max_remount_total = _max_sample_timing(
        lsp_samples,
        "lsp.session.remount_count_total",
    )
    assert max_start_delta == 0.0, (
        f"warm LSP path restarted Pyright: start_count_delta={max_start_delta}"
    )
    assert max_remount_delta > 0.0 or max_remount_total > 0.0, (
        "LSP samples did not expose a remount after shell/edit writes"
    )
    for tool_name in _LSP_NAMES:
        if _tool_count(perf, tool_name) <= 0:
            continue
        warm_samples = [
            sample for sample in _tool_samples(perf, tool_name) if _is_lsp_no_refresh_sample(sample)
        ]
        assert warm_samples, f"{tool_name} has no no-refresh warm samples"
        p95_ms = _warm_tool_p95_ms(
            perf,
            tool_name,
            include_sample=_is_lsp_no_refresh_sample,
        )
        assert p95_ms <= _WARM_LSP_NO_REFRESH_P95_BUDGET_MS, (
            f"{tool_name} no-refresh warm p95 {p95_ms:.3f}ms exceeds "
            f"{_WARM_LSP_NO_REFRESH_P95_BUDGET_MS:.0f}ms"
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
    assert report.passed_sandbox_checks, [item for item in report.sandbox_checks if not item.passed]

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
    runner_event_values = {event.value for event in EventType}
    logged_events = {
        EventType(event_type)
        for row in _jsonl_rows(sandbox_log)
        if (event_type := row.get("event_type")) in runner_event_values
    }
    missing_logged = sorted(
        event.value for event in contract.required_sandbox_events if event not in logged_events
    )
    assert not missing_logged, f"missing persisted events: {missing_logged}"

    if contract.require_squash_events:
        squash_event_count = sum(
            1
            for event in report.events
            if event.type == EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED
        )
        assert squash_event_count >= 10, f"only {squash_event_count} squash events; expected >= 10"

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
        name: sum(1 for c in report.tool_calls if c.tool_name == name) for name in _LSP_NAMES
    }
    weak = [name for name, cnt in lsp_counts.items() if cnt < contract.lsp_floor]
    assert not weak, f"LSP tools below floor {contract.lsp_floor}: {lsp_counts}"

    caller = SandboxCaller(agent_id="complex-project-build-test")
    perf = await _read_json(sandbox_id, BUILD_METRICS_PATH, caller)
    assert perf.get("schema") == PERF_SCHEMA
    for top_key in ("tool_use", "layer_stack", "overlay", "occ", "phases"):
        assert top_key in perf, f"perf.json missing {top_key!r}: keys={list(perf.keys())}"

    probe_tool_calls = [c for c in report.tool_calls if c.tool_name not in _NON_PROBE_TOOL_NAMES]
    perf_total_calls = int(perf["tool_use"].get("total_calls") or 0)
    assert abs(perf_total_calls - len(probe_tool_calls)) <= 5, (
        f"perf.tool_use.total_calls={perf_total_calls} vs "
        f"probe-tool-calls={len(probe_tool_calls)} "
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
        assert float(layer.get("max_depth_before") or 0.0) > float(AUTO_SQUASH_MAX_DEPTH)

    summary = await _read_json(
        sandbox_id,
        f"{WORKSPACE_ROOT}/.metrics/summary.json",
        caller,
    )
    api_calls = summary.get("api_calls") or {}
    api_read_count = int(api_calls.get("read") or 0)
    api_edit_count = int(api_calls.get("edit") or 0)
    api_shell_count = int(api_calls.get("shell") or 0)
    assert api_read_count >= contract.api_read_floor, f"api.read_file count={api_read_count}"
    assert api_edit_count >= contract.api_edit_floor, f"api.edit_file count={api_edit_count}"
    assert api_shell_count >= contract.api_shell_floor, f"api.shell count={api_shell_count}"

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
    assert report.passed_sandbox_checks, [item for item in report.sandbox_checks if not item.passed]

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
    assert abs(float(routing["shell_edit_ratio"]) - (1 / 3)) <= (contract.shell_ratio_tolerance)
    assert routing["routing_rule"] == "logical_edit_index % 3 == 2"

    lsp_correctness = summary["lsp_correctness"]
    assert int(lsp_correctness["total_checks"]) >= contract.total_lsp_floor
    assert int(lsp_correctness["failed_checks"]) == 0
    assert int(lsp_correctness["passed_checks"]) == int(lsp_correctness["total_checks"])
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
    assert int(diagnostic_probe["diagnostic_checks"]) >= (contract.diagnostic_checks_floor)

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
    assert report.passed_sandbox_checks, [item for item in report.sandbox_checks if not item.passed]

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
    assert {"files_with_matches", "count", "content"}.issubset(set(grep_glob["grep_modes"]))
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
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def _load_task_center_performance_report(
    report: RunReport,
) -> Mapping[str, Any]:
    task = getattr(report, "performance_report_task", None)
    assert task is not None, "run did not schedule performance_report.json"
    perf_path = await task
    assert perf_path == report.run_dir / "performance_report.json"
    return load_performance_report(report.run_dir)


def _assert_project_build_per_tool_report(
    perf: Mapping[str, Any],
    *,
    required_tools: Sequence[str],
) -> None:
    per_tool = mapping(mapping(perf["tools"])["per_tool"])
    missing = [tool for tool in required_tools if tool not in per_tool]
    assert not missing, f"performance_report.json missing tools: {missing}"
    for tool_name in required_tools:
        stats = mapping(per_tool[tool_name])
        assert int(stats.get("count") or 0) > 0, f"{tool_name} count is zero"
        for key in ("p50_ms", "p95_ms", "max_ms"):
            assert key in stats, f"{tool_name} missing {key}"
            assert float(stats[key]) >= 0.0, f"{tool_name}.{key}={stats[key]}"


def _assert_workspace_tree_is_o1(run_dir: Path, perf: Mapping[str, Any]) -> None:
    assert_o1_workspace_resource_snapshots(run_dir / "sandbox_events.jsonl")
    assert_resource_key_max(perf, "resource.command_exec.workspace_tree_bytes", 0.0)
    assert_resource_key_max(perf, "resource.command_exec.workspace_tree_exists", 0.0)


def _assert_direct_file_samples_do_not_create_overlay_resources(
    perf: Mapping[str, Any],
) -> None:
    violations: list[str] = []
    for tool_name in _DIRECT_FILE_TOOLS:
        for sample in _tool_samples(perf, tool_name):
            timings = mapping(sample.get("timings_s") or {})
            for key in _DIRECT_FILE_OVERLAY_RESOURCE_KEYS:
                value = float(timings.get(key) or 0.0)
                if value:
                    violations.append(f"{tool_name}:{key}={value}")
    assert not violations, "direct file verbs exposed overlay command resources: " + ", ".join(
        violations[:10]
    )


def _assert_upperdir_within_single_operation_budget(perf: Mapping[str, Any]) -> None:
    resources = mapping(mapping(perf["sandbox"])["resource_keys"])
    key = "resource.command_exec.upperdir_tree_bytes"
    assert key in resources, f"missing resource key: {key}"
    upperdir_max = float(mapping(resources[key]).get("max") or 0.0)
    assert upperdir_max <= _PROJECT_BUILD_UPPERDIR_BUDGET_BYTES, (
        f"upperdir max {upperdir_max:.0f} exceeds "
        f"{_PROJECT_BUILD_UPPERDIR_BUDGET_BYTES} byte single-operation budget"
    )
    for truncated_key in (
        "resource.command_exec.upperdir_tree_truncated",
        "resource.command_exec.run_dir_tree_truncated",
        "resource.command_exec.workspace_tree_truncated",
    ):
        assert float(mapping(resources[truncated_key])["max"]) == 0.0


def _assert_manifest_depth_within_squash_target(perf: Mapping[str, Any]) -> None:
    resources = mapping(mapping(perf["sandbox"])["resource_keys"])
    key = "resource.layer_stack.manifest_depth"
    assert key in resources, f"missing resource key: {key}"
    manifest_depth_max = float(mapping(resources[key]).get("max") or 0.0)
    assert manifest_depth_max <= float(AUTO_SQUASH_MAX_DEPTH), (
        f"manifest depth max {manifest_depth_max:.0f} exceeds "
        f"AUTO_SQUASH_MAX_DEPTH={AUTO_SQUASH_MAX_DEPTH}"
    )
    assert_timing_keys_present(perf, ("layer_stack.auto_squash.total_s",))


def _assert_tool_samples_lack_publish_timings(
    perf: Mapping[str, Any],
    tool_name: str,
) -> None:
    violating_keys: set[str] = set()
    for sample in _tool_samples(perf, tool_name):
        timings = mapping(sample.get("timings_s") or {})
        for key in timings:
            if key.startswith(_SEARCH_PUBLISH_TIMING_PREFIXES):
                violating_keys.add(key)
    assert not violating_keys, (
        f"{tool_name} read-only samples published or advanced manifest: {sorted(violating_keys)}"
    )


def _assert_no_internal_sandbox_errors(run_dir: Path) -> None:
    events_path = run_dir / "sandbox_events.jsonl"
    assert events_path.exists(), events_path
    raw = events_path.read_text(encoding="utf-8", errors="replace")
    forbidden = (
        "internal_error",
        "stale lowerdir",
        "manifest references missing layer",
        "mount_failed",
    )
    for needle in forbidden:
        assert needle not in raw, f"{needle!r} appears in {events_path}"


def _tool_count(perf: Mapping[str, Any], tool_name: str) -> int:
    per_tool = mapping(mapping(perf["tools"])["per_tool"])
    if tool_name not in per_tool:
        return 0
    return int(mapping(per_tool[tool_name]).get("count") or 0)


def _tool_samples(perf: Mapping[str, Any], tool_name: str) -> list[Mapping[str, Any]]:
    per_tool = mapping(mapping(perf["tools"])["per_tool"])
    if tool_name not in per_tool:
        return []
    return [mapping(sample) for sample in list(mapping(per_tool[tool_name]).get("samples") or ())]


def _tool_samples_by_prefix(
    perf: Mapping[str, Any],
    prefix: str,
) -> list[Mapping[str, Any]]:
    per_tool = mapping(mapping(perf["tools"])["per_tool"])
    samples: list[Mapping[str, Any]] = []
    for tool_name, stats in per_tool.items():
        if str(tool_name).startswith(prefix):
            samples.extend(mapping(sample) for sample in mapping(stats).get("samples") or ())
    return samples


def _max_sample_timing(samples: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(mapping(sample.get("timings_s") or {}).get(key) or 0.0) for sample in samples]
    return max(values) if values else 0.0


def _warm_tool_p95_ms(
    perf: Mapping[str, Any],
    tool_name: str,
    *,
    include_sample: Callable[[Mapping[str, Any]], bool] | None = None,
) -> float:
    samples = _tool_samples(perf, tool_name)
    warm_samples = samples[2:]
    if include_sample is not None:
        warm_samples = [sample for sample in warm_samples if include_sample(sample)]
    durations = [float(sample["duration_ms"]) for sample in warm_samples if "duration_ms" in sample]
    if len(durations) >= 2:
        return float(statistics.quantiles(durations, n=20, method="inclusive")[18])
    if len(durations) == 1:
        return durations[0]
    per_tool = mapping(mapping(perf["tools"])["per_tool"])
    return float(mapping(per_tool[tool_name]).get("p95_ms") or 0.0)


def _is_lsp_no_refresh_sample(sample: Mapping[str, Any]) -> bool:
    timings = mapping(sample.get("timings_s") or {})
    return (
        float(timings.get("lsp.session.start_count_delta") or 0.0) == 0.0
        and float(timings.get("lsp.session.refresh_count_delta") or 0.0) == 0.0
        and float(timings.get("lsp.session.remount_count_delta") or 0.0) == 0.0
    )
