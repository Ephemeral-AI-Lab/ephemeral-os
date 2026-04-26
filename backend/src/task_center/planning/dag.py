"""DAG plan compiler — validate and compile a flat task list into a dep map.

A plan is a flat list of ``{id, deps}`` entries plus a mapping from id to
input string. Each entry's ``deps`` lists its DIRECT dependencies; transitive
deps are implicit via the graph.
"""

from __future__ import annotations

from typing import Any

from task_center.planning.errors import PlanValidationError


def compile_dag(
    tasks: list[dict[str, Any]],
    task_inputs: dict[str, str],
) -> dict[str, frozenset[str]]:
    """Validate a flat DAG plan and compile it into a direct-dep map.

    Validations: non-empty inputs; entry shape; ids unique and present in
    ``task_inputs``; deps reference known ids, no self-dep, no duplicates;
    no cycles.
    """
    if not isinstance(tasks, list) or len(tasks) == 0:
        raise PlanValidationError("tasks must be a non-empty list")
    if not isinstance(task_inputs, dict) or len(task_inputs) == 0:
        raise PlanValidationError("task_inputs must be a non-empty dict")

    deps: dict[str, frozenset[str]] = {}

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
                f"task {task_id!r}: 'deps' must be a list, got {type(raw_deps).__name__}"
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


def _check_no_cycles(deps: dict[str, frozenset[str]]) -> None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(deps, WHITE)

    def visit(tid: str, stack: list[str]) -> None:
        color[tid] = GRAY
        for dep in deps.get(tid, frozenset()):
            if color.get(dep) == GRAY:
                cycle_path = " -> ".join(stack[stack.index(dep):] + [dep])
                raise PlanValidationError(f"cycle detected in plan: {cycle_path}")
            if color.get(dep) == WHITE:
                visit(dep, stack + [dep])
        color[tid] = BLACK

    for tid in list(deps):
        if color[tid] == WHITE:
            visit(tid, [tid])
