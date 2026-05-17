"""Live regression for partial-parent planner variant routing."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
async def test_partial_parent_routes_child_planner_to_full_only_agent_md(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario = SCENARIO_REGISTRY["pipeline.partial_parent_planner_full_only"]()
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
    )

    assert report.task_center_status == "done", report.metrics
    planner_launches = [
        launch.agent_name for launch in report.launches if launch.role == "planner"
    ]
    assert planner_launches == [
        "planner",
        "planner_full_only",
        "planner",
    ]
    assert _tool_count(report.tool_calls, "submit_plan_continues_goal") == 1
    assert _tool_count(report.tool_calls, "submit_plan_closes_goal") == 2
    _assert_partial_parent_graph(report.graph_summary)
    _assert_full_only_agent_md_was_recorded(report.run_dir)


def _tool_count(tool_calls: list[Any], tool_name: str) -> int:
    return sum(1 for call in tool_calls if call.tool_name == tool_name)


def _assert_partial_parent_graph(graph_summary: dict[str, Any]) -> None:
    goals = graph_summary["goals"]
    assert len(goals) == 2, graph_summary
    root = next(
        goal
        for goal in goals
        if str(goal["requested_by_task_id"]).endswith(":entry")
    )
    child = next(
        goal
        for goal in goals
        if not str(goal["requested_by_task_id"]).endswith(":entry")
    )

    assert len(root["iterations"]) == 2
    assert root["iterations"][0]["attempts"][-1]["continuation_goal"]
    assert str(child["requested_by_task_id"]).endswith(":delegate_child")


def _assert_full_only_agent_md_was_recorded(run_dir: Path) -> None:
    prompts = list(_system_prompts_for(run_dir, "planner_full_only"))
    assert prompts, f"no planner_full_only system prompt in {run_dir}"
    assert any("Continuing the goal is disabled" in prompt for prompt in prompts)
    assert all("submit_plan_continues_goal" not in prompt for prompt in prompts)


def _system_prompts_for(run_dir: Path, agent_name: str) -> Iterator[str]:
    for path in run_dir.rglob("message.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            metadata = row.get("metadata") or {}
            if metadata.get("agent_name") != agent_name or row.get("role") != "system":
                continue
            yield "\n".join(
                str(block.get("text") or "")
                for block in row.get("content", [])
                if isinstance(block, dict)
            )
