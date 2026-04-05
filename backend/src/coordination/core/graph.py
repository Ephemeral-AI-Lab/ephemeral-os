"""Task graph validation algorithms.

Pure validation on TeamTask dicts. Both planning and execution depend on these.
Runtime graph algorithms (get_ready_tasks) live in engine/graph.py.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coordination.core.models import TeamTask


@dataclass
class ValidationIssue:
    """A single validation problem in a task graph."""

    kind: str  # "empty" | "missing_dependency" | "self_reference" | "cycle"
    task_id: str | None  # None for graph-level issues like "empty"
    detail: str


def collect_task_graph_errors(tasks: dict[str, "TeamTask"]) -> list[ValidationIssue]:
    """Collect all structural issues in the task graph without raising.

    Returns an empty list when the graph is valid.
    """
    issues: list[ValidationIssue] = []

    if not tasks:
        issues.append(
            ValidationIssue(kind="empty", task_id=None, detail="Task graph is empty")
        )
        return issues

    all_ids = set(tasks.keys())

    # Missing dependency references
    for task in tasks.values():
        missing = set(task.depends_on) - all_ids
        if missing:
            issues.append(
                ValidationIssue(
                    kind="missing_dependency",
                    task_id=task.task_id,
                    detail=f"Task '{task.task_id}' depends on unknown task(s): {sorted(missing)}",
                )
            )

    # Self-references
    for task in tasks.values():
        if task.task_id in task.depends_on:
            issues.append(
                ValidationIssue(
                    kind="self_reference",
                    task_id=task.task_id,
                    detail=f"Task '{task.task_id}' depends on itself",
                )
            )

    # Cycle detection via Kahn's algorithm (only if no structural errors above)
    if not any(i.kind in ("missing_dependency", "self_reference") for i in issues):
        in_degree: dict[str, int] = {tid: len(tasks[tid].depends_on) for tid in tasks}
        queue: deque[str] = deque(
            tid for tid, deg in in_degree.items() if deg == 0
        )
        processed = 0
        edges: dict[str, list[str]] = {tid: [] for tid in tasks}
        for task in tasks.values():
            for dep_id in task.depends_on:
                edges[dep_id].append(task.task_id)
        while queue:
            current = queue.popleft()
            processed += 1
            for dependent in edges[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        if processed != len(tasks):
            issues.append(
                ValidationIssue(
                    kind="cycle",
                    task_id=None,
                    detail=f"Task graph contains a cycle (processed {processed}/{len(tasks)} tasks)",
                )
            )

    return issues


def validate_task_graph(tasks: dict[str, "TeamTask"]) -> None:
    """Validate the task graph structure.

    Raises ValueError on any structural problem.
    """
    issues = collect_task_graph_errors(tasks)
    if issues:
        details = "; ".join(i.detail for i in issues)
        raise ValueError(details)
