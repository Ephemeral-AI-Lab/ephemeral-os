"""Heavy live regression for concurrent layer-stack, overlay, and OCC pressure."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox.api import ReadFileRequest, SandboxCaller
from task_center_runner.agent.mock.high_concurrency_probe import (
    CONFLICT_WORKER_COUNT,
    DATA_FILES_PER_WORKER,
    SUMMARY_PATH,
    SUMMARY_SCHEMA,
)
from task_center_runner.benchmarks.sweevo.fixtures import run_sweevo_scenario
from task_center_runner.core.runner import RunReport
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.audit.events import EventType
from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.scenarios.sandbox.high_concurrency_layerstack_overlay_occ import (
    WORKER_COUNT,
)
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
@pytest.mark.timeout(1800)
async def test_high_concurrency_layerstack_overlay_occ_capacity(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.high_concurrency_layerstack_overlay_occ"]
    scenario = scenario_cls()
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )

    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    summary = await _read_summary(sandbox_id)
    _assert_summary(summary)
    _assert_report_shape(report, summary)
    _assert_sandbox_events(report.run_dir / "sandbox_events.jsonl", summary)
    await _assert_performance_report(report, summary)


def _assert_summary(summary: Mapping[str, Any]) -> None:
    assert summary["schema"] == SUMMARY_SCHEMA
    assert summary["worker_count"] == WORKER_COUNT
    assert summary["worker_indexes"] == list(range(WORKER_COUNT))
    assert int(summary["conflict_successes"]) >= 1
    assert int(summary["conflict_errors"]) >= 1
    assert (
        int(summary["conflict_successes"]) + int(summary["conflict_errors"])
        == CONFLICT_WORKER_COUNT
    )
    assert int(summary["total_write_calls"]) == WORKER_COUNT * DATA_FILES_PER_WORKER
    assert int(summary["total_edit_calls"]) == (
        WORKER_COUNT * DATA_FILES_PER_WORKER + CONFLICT_WORKER_COUNT
    )
    assert int(summary["total_read_calls"]) == WORKER_COUNT * 2
    assert int(summary["total_shell_calls"]) == WORKER_COUNT


def _assert_report_shape(report: RunReport, summary: Mapping[str, Any]) -> None:
    counts = Counter(event.type for event in report.events)
    assert counts[EventType.EXECUTOR_SUCCESS] >= WORKER_COUNT + 2
    assert counts[EventType.SANDBOX_CONFLICT_DETECTED] >= int(
        summary["conflict_errors"]
    )

    expected = tuple(SCENARIO_REGISTRY[report.scenario_name]().expected_event_sequence)
    position = 0
    for event_type in report.seen_event_types:
        if position < len(expected) and event_type == expected[position]:
            position += 1
    assert position == len(expected), [
        event_type.value for event_type in report.seen_event_types
    ]

    error_calls = [call for call in report.tool_calls if call.is_error]
    assert len(error_calls) == int(summary["conflict_errors"])
    assert {call.tool_name for call in error_calls} == {"edit_file"}

    tool_counts = Counter(call.tool_name for call in report.tool_calls)
    assert tool_counts["write_file"] >= (
        WORKER_COUNT * DATA_FILES_PER_WORKER + WORKER_COUNT + 2
    )
    assert tool_counts["edit_file"] >= (
        WORKER_COUNT * DATA_FILES_PER_WORKER + CONFLICT_WORKER_COUNT
    )
    assert tool_counts["read_file"] >= WORKER_COUNT * 2 + 1
    assert tool_counts["shell"] >= WORKER_COUNT + 2


def _assert_sandbox_events(path: Path, summary: Mapping[str, Any]) -> None:
    assert path.exists()
    rows = _jsonl_rows(path)
    counts = Counter(row.get("event_type") for row in rows)
    assert counts[EventType.SANDBOX_OVERLAY_EXECUTED.value] >= WORKER_COUNT + 2
    assert counts[EventType.SANDBOX_OCC_CHANGESET_RECEIVED.value] >= (
        WORKER_COUNT * DATA_FILES_PER_WORKER
    )
    assert counts[EventType.SANDBOX_OCC_CHANGES_COMMITTED.value] >= (
        WORKER_COUNT * DATA_FILES_PER_WORKER
    )
    assert counts[EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED.value] >= 1
    assert counts[EventType.SANDBOX_CONFLICT_DETECTED.value] >= int(
        summary["conflict_errors"]
    )


async def _assert_performance_report(
    report: RunReport,
    summary: Mapping[str, Any],
) -> None:
    assert report.performance_report_task is not None
    perf_path = await report.performance_report_task
    assert perf_path == report.run_dir / "performance_report.json"
    perf = json.loads(perf_path.read_text(encoding="utf-8"))
    assert perf["schema"] == "task_center_runner.performance_report.v2"

    totals = _mapping(perf["totals"])
    assert int(totals["tool_errors_total"]) == int(summary["conflict_errors"])
    assert int(totals["tool_calls_total"]) >= (
        WORKER_COUNT * (DATA_FILES_PER_WORKER * 2 + 3)
    )

    per_tool = _mapping(_mapping(perf["tools"])["per_tool"])
    assert int(_mapping(per_tool["write_file"])["count"]) >= (
        WORKER_COUNT * DATA_FILES_PER_WORKER + WORKER_COUNT + 2
    )
    assert int(_mapping(per_tool["edit_file"])["errors"]) == int(
        summary["conflict_errors"]
    )
    assert int(_mapping(per_tool["shell"])["count"]) >= WORKER_COUNT + 2

    sandbox = _mapping(perf["sandbox"])
    event_counts = _mapping(sandbox["event_type_counts"])
    assert int(event_counts[EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED.value]) >= 1
    assert int(event_counts[EventType.SANDBOX_RESOURCE_SNAPSHOT.value]) >= 1

    families = _mapping(sandbox["families"])
    assert int(_mapping(families["occ"])["conflict_count"]) >= int(
        summary["conflict_errors"]
    )
    assert int(_mapping(families["overlay"])["event_count"]) >= WORKER_COUNT + 2
    assert int(_mapping(families["layer_stack"])["event_count"]) >= 1

    resource_keys = _mapping(sandbox["resource_keys"])
    assert "resource.command_exec.changed_path_count" in resource_keys
    for key in (
        "resource.command_exec.run_dir_tree_truncated",
        "resource.command_exec.upperdir_tree_truncated",
        "resource.command_exec.workspace_tree_truncated",
    ):
        assert float(_mapping(resource_keys[key])["max"]) == 0.0


async def _read_summary(sandbox_id: str) -> dict[str, Any]:
    caller = SandboxCaller(agent_id="sweevo-high-concurrency-test")
    result = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=SUMMARY_PATH, caller=caller),
    )
    assert result.success
    assert result.exists
    return json.loads(result.content)


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _mapping(value: object) -> Mapping[str, Any]:
    assert isinstance(value, dict), value
    return value
