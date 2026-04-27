"""DAG plan helpers owned by the task graph layer.

A submitted plan is a flat list of ``{id, deps}`` entries plus a mapping from
task id to input string. This module validates that wire shape, compiles direct
dependency sets, computes graph sinks, and checks global task-id availability.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from task_center.errors import TaskCenterError
from task_center.graph.errors import PlanValidationError
from task_center.model import TaskId

if TYPE_CHECKING:
    from task_center.graph.store import TaskGraph


def compile_dag(
    tasks: list[dict[str, Any]],
    task_inputs: dict[str, str],
) -> dict[TaskId, frozenset[TaskId]]:
    """Validate a flat DAG plan and compile it into a direct-dep map."""
    if not isinstance(tasks, list) or len(tasks) == 0:
        raise PlanValidationError("tasks must be a non-empty list")
    if not isinstance(task_inputs, dict) or len(task_inputs) == 0:
        raise PlanValidationError("task_inputs must be a non-empty dict")

    deps: dict[TaskId, frozenset[TaskId]] = {}

    for entry in tasks:
        if not isinstance(entry, dict):
            raise PlanValidationError(
                f"entries must be objects with 'id', got {entry!r}"
            )
        if "id" not in entry:
            raise PlanValidationError(f"entry missing 'id': {entry!r}")
        task_id = entry["id"]
        if not isinstance(task_id, str) or not task_id:
            raise PlanValidationError(
                f"entry 'id' must be a non-empty string, got {task_id!r}"
            )
        if task_id in deps:
            raise PlanValidationError(f"duplicate task id {task_id!r}")
        if task_id not in task_inputs:
            raise PlanValidationError(
                f"task id {task_id!r} is not a key in task_inputs"
            )

        raw_deps = entry.get("deps", [])
        if not isinstance(raw_deps, list):
            raise PlanValidationError(
                f"task {task_id!r}: 'deps' must be a list, "
                f"got {type(raw_deps).__name__}"
            )
        if len(raw_deps) != len(set(raw_deps)):
            raise PlanValidationError(
                f"task {task_id!r}: 'deps' contains duplicate ids"
            )
        for dep_id in raw_deps:
            if not isinstance(dep_id, str):
                raise PlanValidationError(
                    f"task {task_id!r}: 'deps' entry must be a string, got {dep_id!r}"
                )
            if dep_id == task_id:
                raise PlanValidationError(
                    f"task {task_id!r}: 'deps' may not contain the entry's own id"
                )
        deps[task_id] = frozenset(raw_deps)

    for task_id, dep_set in deps.items():
        for dep_id in dep_set:
            if dep_id not in deps:
                raise PlanValidationError(
                    f"task {task_id!r}: 'deps' references unknown id {dep_id!r}"
                )

    for task_id, task_input in task_inputs.items():
        if task_id not in deps:
            raise PlanValidationError(
                f"task_inputs key {task_id!r} has no matching tasks entry"
            )
        if not isinstance(task_input, str) or not task_input:
            raise PlanValidationError(
                f"task_inputs[{task_id!r}] must be a non-empty string"
            )

    _check_no_cycles(deps)
    return deps


def plan_sinks(deps: Mapping[TaskId, frozenset[TaskId]]) -> frozenset[TaskId]:
    """Return task ids with no outgoing dependency consumers in the submitted DAG."""
    depended_upon: set[TaskId] = set()
    for dep_set in deps.values():
        depended_upon.update(dep_set)
    return frozenset(tid for tid in deps if tid not in depended_upon)


def validate_task_ids_available(graph: "TaskGraph", task_ids: set[TaskId]) -> None:
    """Reject submitted ids that already exist in the live task graph."""
    existing_ids = task_ids & set(graph.tasks)
    if existing_ids:
        first = sorted(existing_ids)[0]
        raise TaskCenterError(f"task id {first!r} already exists in graph")


def _check_no_cycles(deps: dict[TaskId, frozenset[TaskId]]) -> None:
    white, gray, black = 0, 1, 2
    color: dict[TaskId, int] = dict.fromkeys(deps, white)

    def visit(tid: TaskId, stack: list[TaskId]) -> None:
        color[tid] = gray
        for dep in deps.get(tid, frozenset()):
            if color.get(dep) == gray:
                cycle_path = " -> ".join(stack[stack.index(dep):] + [dep])
                raise PlanValidationError(f"cycle detected in plan: {cycle_path}")
            if color.get(dep) == white:
                visit(dep, stack + [dep])
        color[tid] = black

    for tid in list(deps):
        if color[tid] == white:
            visit(tid, [tid])
