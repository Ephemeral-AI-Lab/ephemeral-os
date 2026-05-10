"""Live regression for the dynamic full_case_user_input scenario."""

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
from live_e2e.scenarios.full_case_user_input import (
    FullCaseUserInput,
)
from live_e2e.stores import create_per_test_task_center_stores
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


def test_sweevo_instance_fixture_default_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_SWEEVO_INSTANCE", raising=False)
    instance_id = os.getenv("EOS_SWEEVO_INSTANCE", _DEFAULT_INSTANCE_ID)
    assert select_sweevo_instance(instance_id=instance_id).instance_id == (
        _DEFAULT_INSTANCE_ID
    )


@pytest.mark.asyncio
async def test_full_case_user_input_runs_dynamic_verifier_dag(
    sweevo_instance: SWEEvoInstance,
    sweevo_sandbox: dict[str, object],
    audit_dir: Path,
) -> None:
    _require_daytona_healthy()

    scenario = FullCaseUserInput()
    bundle = create_per_test_task_center_stores()
    try:
        report = await run_sweevo_scenario(
            scenario,
            instance=sweevo_instance,
            sandbox_id=str(sweevo_sandbox["sandbox_id"]),
            audit_dir=audit_dir,
            stores=bundle,
            extra_hooks=(
                count_events(EventType.VERIFIER_FAILURE, name="verifier_failures"),
                assert_recursive_mission_closed_before_parent_guard(),
            ),
        )
    finally:
        bundle.close()

    assert report.task_center_status == "done", report.metrics
    assert report.instance_id == sweevo_instance.instance_id
    assert report.instance_id == _DEFAULT_INSTANCE_ID

    expected_prompt = build_sweevo_user_prompt(sweevo_instance)
    assert report.entry_prompt_length == len(expected_prompt)
    assert report.entry_prompt_sha256 == hashlib.sha256(
        expected_prompt.encode("utf-8")
    ).hexdigest()

    run_payload = json.loads(
        (report.run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert run_payload["instance_id"] == _DEFAULT_INSTANCE_ID

    assert len(report.requirement_ledger) > 100
    executor_count = sum(1 for launch in report.launches if launch.agent_name == "executor")
    verifier_count = sum(1 for launch in report.launches if launch.agent_name == "verifier")
    assert executor_count >= 12
    assert verifier_count >= 4
    assert verifier_count < executor_count

    assert any(
        event.type == EventType.PLANNER_PARTIAL_PLAN for event in report.events
    )
    assert _continuation_episodes_follow_partial_attempts(report.graph_summary)
    assert _has_multi_dependency_verifier(report.graph_summary)
    assert any(event.type == EventType.VERIFIER_FAILURE for event in report.events)
    assert any(
        item.agent_name == "planner" and item.checks.get("failed_attempts")
        for item in report.prompt_inspections
    )

    recursive_requested = [
        event
        for event in report.events
        if event.type == EventType.RECURSIVE_MISSION_REQUESTED
    ]
    assert recursive_requested
    assert _recursive_mission_count(report.graph_summary) >= 1
    _assert_event_order(
        report.events,
        first=EventType.RECURSIVE_MISSION_COMPLETED,
        second=EventType.VERIFIER_SUCCESS,
        second_checkpoint="recursive_return",
    )
    _assert_event_order(
        report.events,
        first=EventType.VERIFIER_SUCCESS,
        second=EventType.EVALUATOR_INVOKED,
        first_checkpoint="final_release",
    )

    _assert_audit_tree_roles(report.run_dir)
    _assert_message_jsonl_contains_tool_scripts(report.run_dir)
    _assert_parallel_agent_execution(report.events)
    _assert_sandbox_monitor_events(report)
    await _assert_daytona_workspace_tool_state(report.sandbox_id)


def _continuation_episodes_follow_partial_attempts(
    graph_summary: dict[str, Any],
) -> bool:
    for mission in graph_summary["missions"]:
        episodes = mission["episodes"]
        by_sequence = {episode["sequence_no"]: episode for episode in episodes}
        for episode in episodes:
            if episode["sequence_no"] <= 1:
                continue
            previous = by_sequence[episode["sequence_no"] - 1]
            final_attempt = previous["attempts"][-1]
            if not final_attempt["continuation_goal"]:
                return False
    return True


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


def _assert_audit_tree_roles(run_dir: Path) -> None:
    assert (run_dir / "run.json").exists()
    assert list(run_dir.glob("entry_executor_*"))
    role_segments: set[str] = set()
    for role_dir in run_dir.rglob("[0-9][0-9]_*_*"):
        role_segments.add(role_dir.name.split("_", 2)[1])
        assert (role_dir / "task.json").exists()
    assert {"executor", "verifier", "evaluator"}.issubset(role_segments)
    mission_dirs = sorted(run_dir.glob("mission_*_*"))
    assert mission_dirs
    assert list(run_dir.glob("mission_*_*/episode_*_*"))
    assert list(run_dir.glob("mission_*_*/episode_*_*/attempt_*_*"))
    first_mission = mission_dirs[0]
    mission = _json_file(first_mission / "mission.json")
    requested_by = mission["requested_by_task_id"]
    assert str(requested_by).endswith(":entry")
    episode_files = sorted(first_mission.glob("episode_*_*/episode.json"))
    assert episode_files
    first_episode = _json_file(episode_files[0])
    assert first_episode["attempt_ids"], "first mission must be delegated work"


def _assert_message_jsonl_contains_tool_scripts(run_dir: Path) -> None:
    messages = _message_steps(run_dir)
    assert messages, f"no message.jsonl agent messages under {run_dir}"
    assert all(
        "role" in message and "content" in message for message in messages
    )
    assert all("step_type" not in message for message in messages)
    agents = {
        str((message.get("metadata") or {}).get("agent_name") or "")
        for message in messages
        if isinstance(message.get("metadata"), dict)
    }
    assert {"executor", "verifier"}.issubset(agents)
    tool_calls = {
        str(block.get("name") or "")
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    assert {"write_file", "edit_file", "read_file", "shell"}.issubset(tool_calls)
    assert "system" in {str(message.get("role") or "") for message in messages}
    assert "user" in {str(message.get("role") or "") for message in messages}
    assert "assistant" in {
        str(message.get("role") or "") for message in messages
    }
    assert any(
        block.get("type") == "tool_result"
        and (message.get("metadata") or {}).get("tool_name") == "write_file"
        and not (message.get("metadata") or {}).get("is_error")
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(message.get("metadata"), dict)
    )
    assert any(
        block.get("type") == "tool_result"
        and (message.get("metadata") or {}).get("tool_name") == "edit_file"
        and (message.get("metadata") or {}).get("is_error")
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(message.get("metadata"), dict)
    )


def _assert_parallel_agent_execution(events: list[Event]) -> None:
    starts: dict[str, tuple[Any, str, str, str]] = {}
    intervals: list[tuple[Any, Any, str, str, str]] = []
    for event in events:
        if event.type == EventType.TOOL_CALL_STARTED:
            tool_id = str(event.payload.get("tool_id") or "")
            if not tool_id:
                continue
            starts[tool_id] = (
                event.ts,
                event.node.agent_run_id or "",
                event.node.agent_name or "",
                str(event.payload.get("tool_name") or ""),
            )
        elif event.type in (EventType.TOOL_CALL_COMPLETED, EventType.TOOL_CALL_ERROR):
            tool_id = str(event.payload.get("tool_id") or "")
            start = starts.pop(tool_id, None)
            if start is None:
                continue
            start_ts, agent_run_id, agent_name, tool_name = start
            intervals.append((start_ts, event.ts, agent_run_id, agent_name, tool_name))

    for index, left in enumerate(intervals):
        left_start, left_end, left_run, _, _ = left
        for right in intervals[index + 1 :]:
            right_start, right_end, right_run, _, _ = right
            if left_run and right_run and left_run != right_run:
                if left_start < right_end and right_start < left_end:
                    return
    raise AssertionError("no overlapping tool intervals from distinct agent runs")


def _assert_sandbox_monitor_events(report: Any) -> None:
    required = {
        EventType.SANDBOX_LAYER_STACK_LEASE_ACQUIRED,
        EventType.SANDBOX_LAYER_STACK_LAYER_CREATED,
        EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED,
        EventType.SANDBOX_OVERLAY_EXECUTED,
        EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
        EventType.SANDBOX_OCC_CHANGES_COMMITTED,
        EventType.SANDBOX_CONFLICT_DETECTED,
    }
    seen = {event.type for event in report.events}
    assert required <= seen
    assert int(report.metrics.get("tool_errors_total") or 0) >= 1

    event_log = report.run_dir / "sandbox_events.jsonl"
    assert event_log.exists()
    rows = _jsonl_rows(event_log)
    logged = {EventType(row["event_type"]) for row in rows}
    assert required <= logged


async def _assert_daytona_workspace_tool_state(sandbox_id: str) -> None:
    import sandbox.api as sandbox_api
    from sandbox.api import ReadFileRequest, SandboxCaller, ShellRequest
    from sandbox.host.daemon_client import call_daemon_api

    caller = SandboxCaller(agent_id="sweevo-live-test")
    binding_payload = await call_daemon_api(
        sandbox_id,
        "api.workspace_binding",
        {"actor_id": caller.agent_id},
        timeout=30,
    )
    binding = binding_payload.get("binding")
    assert isinstance(binding, dict), binding_payload
    assert binding.get("workspace_root") == "/testbed"
    assert int(binding.get("base_manifest_version") or 0) >= 1

    proof_path = "/testbed/.ephemeralos/sweevo-mock/full_case/workspace-proof.txt"
    proof = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=proof_path, caller=caller),
    )
    assert proof.success
    assert proof.exists
    assert "declared_workspace=/testbed" in proof.content

    shell = await sandbox_api.shell(
        sandbox_id,
        ShellRequest(
            command=f"test -s {proof_path} && printf 'workspace=/testbed\\n'",
            cwd="/testbed",
            timeout=60,
            caller=caller,
            description="verify SWE-EVO tool state in /testbed",
        ),
    )
    assert shell.success
    assert shell.exit_code == 0
    assert "workspace=/testbed" in shell.stdout


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _message_steps(run_dir: Path) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    message_paths = list(run_dir.rglob("message.jsonl"))
    assert message_paths, f"no message.jsonl files under {run_dir}"
    for path in message_paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                steps.append(json.loads(line))
    return steps
