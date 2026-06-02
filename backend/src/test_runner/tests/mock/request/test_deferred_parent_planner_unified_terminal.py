"""Live regression for partial-parent planner unified terminal."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from test_runner.benchmarks.sweevo.models import SWEEvoInstance
from test_runner.scenarios import SCENARIO_REGISTRY
from test_runner.core.stores import TaskStoreBundle
from test_runner.environments.sweevo_image.fixtures import run_scenario_on_sweevo_image
from test_runner.tests._live_config import (
    database_configured,
    rust_sandbox_runtime_unavailable_reason,
)


pytestmark = pytest.mark.asyncio
_RUST_RUNTIME_UNAVAILABLE = rust_sandbox_runtime_unavailable_reason()


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    _RUST_RUNTIME_UNAVAILABLE is not None,
    reason=_RUST_RUNTIME_UNAVAILABLE or "Rust sandbox runtime unavailable",
)
async def test_partial_parent_uses_unified_planner_terminal(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskStoreBundle,
) -> None:
    scenario = SCENARIO_REGISTRY[
        "pipeline.deferred_parent_planner_unified_terminal"
    ]()
    report = await run_scenario_on_sweevo_image(
        scenario,
        instance=sweevo_image_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
    )

    assert report.request_status == "done", report.metrics
    planner_launches = [
        launch.agent_name for launch in report.launches if launch.role == "planner"
    ]
    assert planner_launches == ["planner", "planner", "planner"]
    assert _tool_count(report.tool_calls, "submit_planner_outcome") == 3
    _assert_partial_parent_graph(report.graph_summary)
    _assert_unified_planner_catalog_was_recorded(report.run_dir)


def _tool_count(tool_calls: list[Any], tool_name: str) -> int:
    return sum(1 for call in tool_calls if call.tool_name == tool_name)


def _assert_partial_parent_graph(graph_summary: dict[str, Any]) -> None:
    workflows = graph_summary["workflows"]
    assert len(workflows) == 2, graph_summary
    # Entry vs nested delegated workflow is classified by parent_task_id:
    # the entry-origin workflow's parent is the root Task, while the nested
    # workflow's parent is the delegating generator task.
    root = next(
        workflow
        for workflow in workflows
        if str(workflow.get("parent_task_id") or "").startswith("root-")
    )
    child = next(
        workflow
        for workflow in workflows
        if not str(workflow.get("parent_task_id") or "").startswith("root-")
    )

    assert len(root["iterations"]) == 2
    assert root["iterations"][0]["attempts"][-1]["deferred_goal_for_next_iteration"]
    assert str(child["parent_task_id"]).endswith(":delegate_child")


def _assert_unified_planner_catalog_was_recorded(run_dir: Path) -> None:
    active_terminal_sets = list(_active_terminal_sets_for(run_dir, "planner"))
    assert active_terminal_sets
    assert set(active_terminal_sets) == {("submit_planner_outcome",)}

    catalogs = list(_terminal_catalog_rows_for(run_dir, "planner"))
    assert catalogs, f"no planner terminal catalog row in {run_dir}"
    assert all("submit_planner_outcome" in catalog for catalog in catalogs)


def _active_terminal_sets_for(
    run_dir: Path,
    agent_name: str,
) -> Iterator[tuple[str, ...]]:
    for path in run_dir.rglob("message.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            metadata = row.get("metadata") or {}
            if metadata.get("agent_name") != agent_name:
                continue
            active = metadata.get("active_terminals")
            if isinstance(active, list):
                yield tuple(str(name) for name in active)


def _terminal_catalog_rows_for(run_dir: Path, agent_name: str) -> Iterator[str]:
    for path in run_dir.rglob("message.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            metadata = row.get("metadata") or {}
            if metadata.get("agent_name") != agent_name or row.get("role") != "user":
                continue
            text = "\n".join(
                str(block.get("text") or "")
                for block in row.get("content", [])
                if isinstance(block, dict)
            )
            if "<terminal_tool_selection>" not in text:
                continue
            yield text.split("<terminal_tool_selection>\n", 1)[1].split(
                "\n</terminal_tool_selection>",
                1,
            )[0]
