"""Live regression for the full_stack_adversarial scenario."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from benchmarks.sweevo.dataset import select_sweevo_instance
from benchmarks.sweevo.prompt import build_sweevo_user_prompt
from live_e2e.audit.events import Event, EventType
from live_e2e.hooks.builtins import (
    assert_recursive_mission_closed_before_parent_guard,
    count_events,
)
from live_e2e.scenarios.full_stack_adversarial import (
    FullStackAdversarial,
)
from live_e2e.stores import TaskCenterStoreBundle
from live_e2e.sweevo_adapter import run_sweevo_scenario
from benchmarks.sweevo.models import SWEEvoInstance


_DEFAULT_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"


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
        "_sweevo_tier0_health", tier0_path
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


def test_full_stack_instance_fixture_default_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EOS_SWEEVO_INSTANCE", raising=False)
    instance_id = os.getenv("EOS_SWEEVO_INSTANCE", _DEFAULT_INSTANCE_ID)
    assert select_sweevo_instance(instance_id=instance_id).instance_id == (
        _DEFAULT_INSTANCE_ID
    )


@pytest.mark.asyncio
async def test_full_stack_adversarial_runs_agent_tool_script_matrix(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    _require_daytona_healthy()

    scenario = FullStackAdversarial()
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
    assert report.instance_id == _DEFAULT_INSTANCE_ID

    expected_prompt = build_sweevo_user_prompt(sweevo_instance)
    assert report.entry_prompt_length == len(expected_prompt)
    assert report.entry_prompt_sha256 == hashlib.sha256(
        expected_prompt.encode("utf-8")
    ).hexdigest()

    assert len(report.requirement_ledger) > 100
    assert len(report.package_plan) >= 4
    assert len(report.matrix_plan) >= 32

    _assert_task_center_shape(report.graph_summary, report.events)
    _assert_message_logs(report.run_dir)
    _assert_sandbox_monitor_events(report.events, report.run_dir)
    await _assert_final_sandbox_state(
        sandbox_id=report.sandbox_id,
        task_center_run_id=report.task_center_run_id,
    )


def _assert_task_center_shape(
    graph_summary: dict[str, Any],
    events: list[Event],
) -> None:
    seen = {event.type for event in events}
    assert EventType.PLANNER_FULL_PLAN in seen
    assert EventType.PLANNER_PARTIAL_PLAN in seen
    assert EventType.VERIFIER_FAILURE in seen
    assert EventType.RECURSIVE_MISSION_REQUESTED in seen
    assert EventType.RECURSIVE_MISSION_COMPLETED in seen
    assert EventType.EVALUATOR_SUCCESS in seen
    assert EventType.FULL_STACK_SCRIPT_COMPLETED in seen
    assert _has_multi_dependency_verifier(graph_summary)
    assert _recursive_mission_count(graph_summary) >= 1
    _assert_event_order(
        events,
        first=EventType.RECURSIVE_MISSION_COMPLETED,
        second=EventType.VERIFIER_SUCCESS,
        second_checkpoint="recursive_return",
    )
    _assert_event_order(
        events,
        first=EventType.VERIFIER_SUCCESS,
        second=EventType.EVALUATOR_INVOKED,
        first_checkpoint="final_release",
    )


def _has_multi_dependency_verifier(graph_summary: dict[str, Any]) -> bool:
    for mission in graph_summary["missions"]:
        for episode in mission["episodes"]:
            for attempt in episode["attempts"]:
                for task in attempt["tasks"]:
                    if task.get("agent_name") == "verifier" and len(task["needs"]) > 1:
                        return True
    return False


def _recursive_mission_count(graph_summary: dict[str, Any]) -> int:
    return sum(
        1
        for mission in graph_summary["missions"]
        if not str(mission["requested_by_task_id"]).endswith(":entry")
    )


def _assert_message_logs(run_dir: Path) -> None:
    messages = _message_rows(run_dir)
    assert messages, f"no message.jsonl agent messages under {run_dir}"
    agents = {
        str((message.get("metadata") or {}).get("agent_name") or "")
        for message in messages
        if isinstance(message.get("metadata"), dict)
    }
    assert {"entry_executor", "planner", "executor", "verifier", "evaluator"} <= agents
    tool_uses = {
        str(block.get("name") or "")
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    assert {
        "write_file",
        "edit_file",
        "read_file",
        "shell",
        "lsp.hover",
        "lsp.find_definitions",
        "lsp.find_references",
        "lsp.diagnostics",
        "lsp.query_symbols",
    } <= tool_uses
    assert any(
        block.get("type") == "tool_result"
        and (message.get("metadata") or {}).get("tool_name") == "edit_file"
        and (message.get("metadata") or {}).get("is_error")
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(message.get("metadata"), dict)
    )


def _assert_sandbox_monitor_events(events: list[Event], run_dir: Path) -> None:
    required = {
        EventType.SANDBOX_LAYER_STACK_LEASE_ACQUIRED,
        EventType.SANDBOX_LAYER_STACK_LAYER_CREATED,
        EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED,
        EventType.SANDBOX_OVERLAY_EXECUTED,
        EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
        EventType.SANDBOX_OCC_CHANGES_COMMITTED,
        EventType.SANDBOX_CONFLICT_DETECTED,
    }
    seen = {event.type for event in events}
    missing = sorted(event.value for event in required - seen)
    assert not missing, f"missing sandbox monitor events: {missing}"

    sandbox_log = run_dir / "sandbox_events.jsonl"
    assert sandbox_log.exists()
    logged = {EventType(row["event_type"]) for row in _jsonl_rows(sandbox_log)}
    missing_logged = sorted(event.value for event in required - logged)
    assert not missing_logged, f"missing persisted sandbox events: {missing_logged}"


async def _assert_final_sandbox_state(
    *,
    sandbox_id: str,
    task_center_run_id: str,
) -> None:
    import sandbox.api as sandbox_api
    from sandbox.api import ReadFileRequest, SandboxCaller, ShellRequest

    caller = SandboxCaller(agent_id="sweevo-full-stack-test")
    final_path = "/testbed/.ephemeralos/sweevo-mock/full_stack/final-reconciliation.json"
    final = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=final_path, caller=caller),
    )
    assert final.success and final.exists
    final_payload = json.loads(final.content)
    assert final_payload["scenario"] == "full_stack_adversarial"
    assert final_payload["failed_cells"] == 0
    assert final_payload["recursive_missions"] == 1
    assert final_payload["manifest_end"] > final_payload["manifest_start"]

    lsp_path = "/testbed/.ephemeralos/sweevo-mock/full_stack/lsp-matrix.json"
    lsp = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=lsp_path, caller=caller),
    )
    assert lsp.success and lsp.exists
    assert json.loads(lsp.content)["subsystem"] == "lsp"

    metrics_path = (
        "/testbed/.omc/results/"
        f"full-stack-adversarial-{_safe_slug(task_center_run_id)}.jsonl"
    )
    metrics = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=metrics_path, caller=caller),
    )
    assert metrics.success and metrics.exists
    rows = [json.loads(line) for line in metrics.content.splitlines() if line.strip()]
    summary_rows = [
        row
        for row in rows
        if row.get("schema") == "full_stack_adversarial.summary.v1"
    ]
    assert summary_rows
    summary = summary_rows[-1]
    assert summary["failed_cells"] == 0
    assert summary["passed_cells"] >= 32
    assert summary["expected_tool_errors"] >= 1
    assert summary["conflicts_detected"] >= 1
    assert any(row.get("subsystem") == "lsp" for row in rows)

    shell = await sandbox_api.shell(
        sandbox_id,
        ShellRequest(
            command=(
                f"test -s {final_path} && test -d /testbed/.git && "
                "printf 'workspace=/testbed\\n'"
            ),
            cwd="/testbed",
            timeout=60,
            caller=caller,
            description="verify final full-stack sandbox state",
        ),
    )
    assert shell.success
    assert shell.exit_code == 0
    assert "workspace=/testbed" in shell.stdout


def _assert_event_order(
    events: list[Event],
    *,
    first: EventType,
    second: EventType,
    first_checkpoint: str | None = None,
    second_checkpoint: str | None = None,
) -> None:
    first_index = _event_index(events, first, first_checkpoint)
    assert first_index >= 0, first
    second_index = _event_index(
        events,
        second,
        second_checkpoint,
        start=first_index + 1,
    )
    assert second_index >= 0, second
    assert first_index < second_index


def _event_index(
    events: list[Event],
    event_type: EventType,
    checkpoint: str | None,
    *,
    start: int = 0,
) -> int:
    for index, event in enumerate(events[start:], start=start):
        if event.type != event_type:
            continue
        if checkpoint is not None and event.payload.get("checkpoint") != checkpoint:
            continue
        return index
    return -1


def _message_rows(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    message_paths = list(run_dir.rglob("message.jsonl"))
    assert message_paths, f"no message.jsonl files under {run_dir}"
    for path in message_paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _safe_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
