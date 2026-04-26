"""Build the prompt sent to an agent for one TaskCenter task."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from task_center.graph import TaskGraph
from task_center.task import Status, Task, TaskSummary


def build_task_prompt(task: Task, graph: TaskGraph) -> str:
    """Return the user/task prompt with stable TaskCenter context injected.

    Planners receive their pre-rendered ``PlannerLaunchContext`` JSON as the
    task input — no extra context wrapper is needed. Executors and evaluators
    get role-specific context.
    """
    if task.role == "planner":
        return task.input

    context = _context_for(task, graph)
    if context is None:
        return task.input
    context_json = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    return (
        f"<Task Context>\n{context_json}\n</Task Context>\n\n"
        f"<Task Prompt>\n{task.input}\n</Task Prompt>"
    )


def _context_for(task: Task, graph: TaskGraph) -> dict[str, Any] | None:
    if task.role == "executor":
        deps = _completed_dependencies(task, graph)
        return {"completed_dependencies": deps} if deps else None

    if task.role == "evaluator":
        if task.task_center_harness_graph_id is None:
            return None
        harness = graph.harness_graphs.get(task.task_center_harness_graph_id)
        if harness is None:
            return None
        parent = graph.tasks.get(harness.parent_task_id)
        planner = graph.tasks.get(harness.planner_task_id)
        completed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for tid in harness.executor_task_ids:
            child = graph.tasks.get(tid)
            if child is None:
                continue
            for s in child.summaries:
                if s.kind == "success":
                    completed.append(_summary_payload(s))
                elif s.kind in ("failure", "dependency_blocked"):
                    failed.append(_summary_payload(s))
        return {
            "parent_goal": parent.input if parent is not None else None,
            "planner_handoff": [
                _summary_payload(s)
                for s in (planner.summaries if planner is not None else [])
                if s.kind == "handoff"
            ],
            "completed_child_summaries": completed,
            "failed_child_summaries": failed,
        }

    return None


def _completed_dependencies(task: Task, graph: TaskGraph) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dep_id in sorted(task.needs):
        dep = graph.tasks.get(dep_id)
        if dep is None or dep.status is not Status.DONE:
            continue
        out.append(
            {
                "id": dep.id,
                "task_input": dep.input,
                "summaries": [_summary_payload(s) for s in dep.summaries],
            }
        )
    return out


def _summary_payload(summary: TaskSummary) -> dict[str, Any]:
    return asdict(summary)
