"""Capacity-suite regression for ``capacity.full_system_capacity_matrix``."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.audit.events import EventType
from task_center_runner.hooks.builtins import (
    assert_recursive_mission_closed_before_parent_guard,
    count_events,
)
from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.stores import TaskCenterStoreBundle
from task_center_runner.sweevo_adapter import run_sweevo_scenario
from sandbox.api import ReadFileRequest, SandboxCaller

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.live_e2e_capacity,
    pytest.mark.live_e2e_daytona,
]

_FORBIDDEN_RUN_SIGNATURES = (
    "internal_error",
    "manifest references missing layer",
    "stale lowerdir",
    ".pyright_scratch",
    "untyped conflict",
)


@pytest.mark.skipif(
    os.environ.get("EPHEMERALOS_RUN_CAPACITY_LIVE_E2E") != "1",
    reason="set EPHEMERALOS_RUN_CAPACITY_LIVE_E2E=1 to run capacity live e2e",
)
@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - task_center_runner requires PostgreSQL",
)
async def test_full_system_capacity_matrix_records_artifacts_and_metrics(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    _require_daytona_healthy()

    scenario = SCENARIO_REGISTRY["capacity.full_system_capacity_matrix"]()
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
        extra_hooks=(
            count_events(EventType.VERIFIER_FAILURE, name="verifier_failures"),
            assert_recursive_mission_closed_before_parent_guard(),
        ),
    )

    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]
    assert report.run_dir.parts[-3:-1] == (
        "scenario_logs",
        "capacity.full_system_capacity_matrix",
    )

    _assert_graph_shape(report.graph_summary)
    _assert_tool_and_event_capacity(report)
    _assert_audit_artifacts(report.run_dir)
    _assert_no_forbidden_signatures(report.run_dir)
    await _assert_capacity_workspace_artifacts(
        report.sandbox_id,
        report.task_center_run_id,
    )


def _require_daytona_healthy() -> None:
    import importlib.util
    import sys

    repo_root = Path(__file__).resolve().parents[5]
    tier0_path = (
        repo_root
        / "backend"
        / "tests"
        / "live_e2e_test"
        / "_tools"
        / "tier0_health.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_capacity_tier0_health", tier0_path
    )
    if spec is None or spec.loader is None:
        pytest.skip(f"tier0_health module not loadable from {tier0_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    result = module.probe_tier0()
    if not result.passed:
        pytest.skip(
            f"Tier-0 health gate failed: api_health={result.api_health!r} "
            f"notes={result.notes!r}"
        )


def _assert_graph_shape(graph_summary: dict[str, Any]) -> None:
    missions = graph_summary["missions"]
    assert len(missions) >= 2, graph_summary
    root = next(
        mission
        for mission in missions
        if str(mission["requested_by_task_id"]).endswith(":entry")
    )
    recursive = [
        mission
        for mission in missions
        if not str(mission["requested_by_task_id"]).endswith(":entry")
    ]
    assert recursive, graph_summary
    assert root["status"] == "succeeded"
    assert all(mission["status"] == "succeeded" for mission in recursive)

    attempts = [
        attempt
        for mission in missions
        for episode in mission["episodes"]
        for attempt in episode["attempts"]
    ]
    tasks = [task for attempt in attempts for task in attempt["tasks"]]
    assert len(root["episodes"]) >= 3
    assert len(attempts) >= 5
    assert len(tasks) >= 20
    assert any(
        task.get("id", "").endswith(":capacity_metrics_summary") for task in tasks
    )
    assert any(
        task.get("agent_name") == "verifier" and len(task["needs"]) > 1
        for task in tasks
    )
    assert max(len(attempt["tasks"]) for attempt in attempts) >= 5


def _assert_tool_and_event_capacity(report: Any) -> None:
    tool_counts = Counter(call.tool_name for call in report.tool_calls)
    assert tool_counts["write_file"] >= 30
    assert tool_counts["edit_file"] >= 5
    assert tool_counts["read_file"] >= 20
    assert tool_counts["shell"] >= 10
    assert (
        sum(count for name, count in tool_counts.items() if name.startswith("lsp."))
        >= 5
    )

    required_events = {
        EventType.PLANNER_PARTIAL_PLAN,
        EventType.VERIFIER_FAILURE,
        EventType.RECURSIVE_MISSION_REQUESTED,
        EventType.RECURSIVE_MISSION_COMPLETED,
        EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED,
        EventType.SANDBOX_OVERLAY_EXECUTED,
        EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
        EventType.SANDBOX_OCC_CHANGES_COMMITTED,
        EventType.SANDBOX_CONFLICT_DETECTED,
        EventType.FULL_STACK_SCRIPT_COMPLETED,
    }
    seen = {event.type for event in report.events}
    missing = sorted(event.value for event in required_events - seen)
    assert not missing, f"missing required events: {missing}"
    assert int(report.metrics.get("tool_errors_total") or 0) >= 1


def _assert_audit_artifacts(run_dir: Path) -> None:
    run_payload = _json_file(run_dir / "run.json")
    assert run_payload["status"] == "finished"
    assert _json_file(run_dir / "metrics.json")["tool_calls_total"] > 0

    task_files = list(run_dir.rglob("task.json"))
    message_files = list(run_dir.rglob("message.jsonl"))
    assert task_files, f"no task.json files under {run_dir}"
    assert message_files, f"no message.jsonl files under {run_dir}"
    assert all(path.stat().st_size > 0 for path in message_files)

    sandbox_log = run_dir / "sandbox_events.jsonl"
    assert sandbox_log.exists()
    sandbox_events = [row["event_type"] for row in _jsonl_rows(sandbox_log)]
    assert EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED.value in sandbox_events
    assert EventType.SANDBOX_CONFLICT_DETECTED.value in sandbox_events


def _assert_no_forbidden_signatures(run_dir: Path) -> None:
    for path in [
        run_dir / "run.json",
        run_dir / "metrics.json",
        *run_dir.rglob("message.jsonl"),
    ]:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for signature in _FORBIDDEN_RUN_SIGNATURES:
            assert signature.lower() not in lowered, f"{signature!r} appeared in {path}"


async def _assert_capacity_workspace_artifacts(
    sandbox_id: str,
    task_center_run_id: str,
) -> None:
    caller = SandboxCaller(agent_id="capacity-full-system-test")
    summary = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(
            path=(
                "/testbed/.ephemeralos/sweevo-mock/capacity/"
                "full-system-capacity-summary.json"
            ),
            caller=caller,
        ),
    )
    assert summary.success and summary.exists
    summary_payload = json.loads(summary.content)
    assert summary_payload["schema"] == "live_e2e.capacity.v1"
    assert summary_payload["scenario"] == "capacity.full_system_capacity_matrix"
    assert summary_payload["task_center_run_id"] == task_center_run_id
    assert summary_payload["graph"]["planned_matrix_cells"] >= 32
    assert summary_payload["tool_use"]["lsp"] >= 5

    planned_graph = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path="/testbed/.metrics/planned_graph.json", caller=caller),
    )
    assert planned_graph.success and planned_graph.exists
    graph_payload = json.loads(planned_graph.content)
    assert graph_payload["schema"] == "live_e2e.capacity.planned_graph.v1"
    assert graph_payload["matrix_cell_count"] >= 32
    assert ["capacity_metrics_summary", "final_release_guard"] in graph_payload[
        "final_edges"
    ]


def _json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
