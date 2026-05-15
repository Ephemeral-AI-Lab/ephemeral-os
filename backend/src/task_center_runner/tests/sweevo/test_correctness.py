"""Live e2e regression for the SWE-EVO mock framework.

Exercises the ``correctness_testing`` scenario end-to-end against a real
Daytona sandbox + the real TaskCenter runtime + the deterministic mock
squad. Verifies the on-disk audit tree, mid-run message.jsonl flushing,
helper-agent filtering, and hook ordering.

Skipped when Daytona is unreachable so unit-test collections that import
this file do not fail. The pytest tier 7 invocation
(``run_tiered.py --tier 7``) provisions a real sandbox and asserts the
test passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from task_center_runner.audit.events import EventType
from task_center_runner.hooks.builtins import count_events
from task_center_runner.scenarios.correctness_testing import (
    CorrectnessTesting,
)
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.benchmarks.sweevo.fixtures import run_sweevo_scenario
from benchmarks.sweevo.models import SWEEvoInstance


def _require_daytona_healthy() -> None:
    """Tier-0 health gate. Skip the test cleanly if Daytona is unavailable."""
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


@pytest.mark.asyncio
async def test_correctness_testing_scenario_runs_end_to_end(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    _require_daytona_healthy()

    scenario = CorrectnessTesting()
    extra_hooks = (
        count_events(EventType.PLANNER_INVOKED, name="planner_invocations"),
        count_events(EventType.EVALUATOR_INVOKED, name="evaluator_invocations"),
    )
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
        extra_hooks=extra_hooks,
    )

    # --- TaskCenter outcome -------------------------------------------
    assert report.task_center_status == "done", (
        f"task center status was {report.task_center_status!r}: {report.metrics}"
    )
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    # --- Goal graph: succeeded goal with delegated iterations ----
    delegated = [
        goal
        for goal in report.graph_summary["goals"]
        if len(goal["iterations"]) >= 1
        and any(ep["trials"] for ep in goal["iterations"])
    ]
    assert delegated, "no goal with trials in graph"
    final_mission = delegated[-1]
    assert final_mission["status"] == "succeeded"

    # --- Audit tree on disk -------------------------------------------
    run_dir = report.run_dir
    assert run_dir.is_dir(), f"run_dir {run_dir} missing"
    assert (run_dir / "run.json").exists()
    assert (run_dir / "metrics.json").exists()

    mission_dirs = list(run_dir.glob("mission_*_*"))
    assert mission_dirs, f"no mission_NN_<id> dir under {run_dir}"
    found_attempt_with_role_dir = False
    for mission_dir in mission_dirs:
        assert (mission_dir / "goal.json").exists()
        for episode_dir in mission_dir.glob("episode_*_*"):
            assert (episode_dir / "iteration.json").exists()
            for attempt_dir in episode_dir.glob("attempt_*_*"):
                assert (attempt_dir / "trial.json").exists()
                role_dirs = list(attempt_dir.glob("[0-9][0-9]_*"))
                assert role_dirs, (
                    f"no NN_<role>_<task_id> dir under {attempt_dir}"
                )
                found_attempt_with_role_dir = True
                for role_dir in role_dirs:
                    assert (role_dir / "task.json").exists()
                    role_segment = role_dir.name.split("_", 2)[1]
                    assert role_segment in {
                        "planner",
                        "executor",
                        "evaluator",
                        "generator",
                    }, role_segment
    assert found_attempt_with_role_dir, "no attempt_NN_<id> dir"

    entry_dirs = list(run_dir.glob("entry_executor_*"))
    assert entry_dirs, "missing entry_executor sibling dir"
    assert (entry_dirs[0] / "task.json").exists()
    _assert_message_jsonl_contains_sandbox_tools(run_dir)

    # --- Helper agents are filtered out -------------------------------
    primary_role_segments = {"planner", "executor", "evaluator", "generator"}
    for role_dir in run_dir.rglob("[0-9][0-9]_*_*"):
        role_segment = role_dir.name.split("_", 2)[1]
        assert role_segment in primary_role_segments, (
            f"helper-role dir leaked into audit tree: {role_dir}"
        )

    # --- Hook insertion ordering --------------------------------------
    assert report.mutable_state_flags.get("count_planner_invocations", 0) >= 1
    assert report.mutable_state_flags.get("count_evaluator_invocations", 0) >= 1
    # Hooks fire in registration order — assert that the planner counter
    # is observed at least once before the final evaluator counter result.
    hook_names = [r.name for r in report.hook_results]
    planner_idx = next(
        (i for i, n in enumerate(hook_names) if "planner_invocations" in n), -1
    )
    evaluator_idx = next(
        (i for i, n in enumerate(hook_names) if "evaluator_invocations" in n), -1
    )
    assert planner_idx >= 0 and evaluator_idx >= 0
    assert planner_idx < evaluator_idx, (
        "planner counter should fire before evaluator counter "
        f"(insertion order): {hook_names}"
    )

    # --- run.json carries the bound run id ----------------------------
    run_payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_payload["task_center_run_id"] == report.task_center_run_id
    assert run_payload["scenario_name"] == scenario.name
    assert run_payload["status"] in {"running", "finished"}


def _assert_message_jsonl_contains_sandbox_tools(run_dir: Path) -> None:
    messages: list[dict[str, object]] = []
    message_paths = list(run_dir.rglob("message.jsonl"))
    assert message_paths, f"no message.jsonl files under {run_dir}"
    for path in message_paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                messages.append(json.loads(line))
    assert all("role" in message and "content" in message for message in messages)
    assert all("step_type" not in message for message in messages)
    assert any(
        block.get("type") == "tool_result"
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict)
    )
    tool_calls = {
        str(block.get("name") or "")
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    assert {"write_file", "read_file", "edit_file", "shell"}.issubset(tool_calls)
