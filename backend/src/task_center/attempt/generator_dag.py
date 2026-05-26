"""Generator DAG helper functions for one harness attempt."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from task_center._core.persistence import TaskRow
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center._core.primitives import generator_task_id
from task_center.task_state import (
    TaskCenterTaskStatus,
    PlannedGeneratorTask,
    TERMINAL_GENERATOR_STATUSES,
)


def ordered_generator_tasks(
    tasks: tuple[PlannedGeneratorTask, ...],
) -> tuple[PlannedGeneratorTask, ...]:
    local_ids: set[str] = set()
    duplicates: list[str] = []
    for task in tasks:
        if task.local_id in local_ids:
            duplicates.append(task.local_id)
        else:
            local_ids.add(task.local_id)
    if duplicates:
        raise TaskCenterInvariantViolation(
            f"Generator plan contains duplicate local ids: {tuple(duplicates)!r}"
        )
    for task in tasks:
        missing = [dep for dep in task.deps if dep not in local_ids]
        if missing:
            raise TaskCenterInvariantViolation(
                f"Generator task {task.local_id!r} has unknown deps: {missing!r}"
            )

    by_id = {task.local_id: task for task in tasks}
    remaining_deps = {task.local_id: set(task.deps) for task in tasks}
    dependents: dict[str, list[str]] = {task.local_id: [] for task in tasks}
    for task in tasks:
        for dep in task.deps:
            dependents[dep].append(task.local_id)

    ready = deque(task.local_id for task in tasks if not task.deps)
    ordered: list[PlannedGeneratorTask] = []
    while ready:
        local_id = ready.popleft()
        ordered.append(by_id[local_id])
        for dependent_id in dependents[local_id]:
            remaining_deps[dependent_id].discard(local_id)
            if not remaining_deps[dependent_id]:
                ready.append(dependent_id)

    if len(ordered) != len(tasks):
        seen_ids = {o.local_id for o in ordered}
        cycle = tuple(t.local_id for t in tasks if t.local_id not in seen_ids)
        raise TaskCenterInvariantViolation(
            f"Generator plan contains a dependency cycle among: {cycle!r}"
        )
    return tuple(ordered)


def dependency_task_ids(
    *,
    attempt_id: str,
    local_deps: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(generator_task_id(attempt_id, dep) for dep in local_deps)


def _task_statuses_by_id(
    task_records: list[TaskRow],
) -> dict[str, TaskCenterTaskStatus]:
    return {task["id"]: TaskCenterTaskStatus(task["status"]) for task in task_records}


def ready_pending_generator_ids(task_records: list[TaskRow]) -> tuple[str, ...]:
    statuses = _task_statuses_by_id(task_records)
    _validate_persisted_deps(task_records, statuses)
    ready: list[str] = []
    for task in task_records:
        if statuses[task["id"]] != TaskCenterTaskStatus.PENDING:
            continue
        needs = tuple(task.get("needs") or ())
        if all(statuses[dep] == TaskCenterTaskStatus.DONE for dep in needs):
            ready.append(task["id"])
    return tuple(ready)


@dataclass(frozen=True, slots=True)
class GeneratorDagSummary:
    all_quiescent: bool
    all_done: bool
    any_failed_or_blocked: bool


_FAILED_OR_BLOCKED = (TaskCenterTaskStatus.FAILED, TaskCenterTaskStatus.BLOCKED)


def _validate_persisted_deps(
    task_records: list[TaskRow],
    statuses: dict[str, TaskCenterTaskStatus],
) -> None:
    for task in task_records:
        missing = [dep for dep in task.get("needs") or () if dep not in statuses]
        if missing:
            raise TaskCenterInvariantViolation(
                f"Generator task {task['id']!r} has unknown persisted deps: {missing!r}"
            )


def _unreachable_pending_ids(
    task_records: list[TaskRow],
    statuses: dict[str, TaskCenterTaskStatus],
) -> frozenset[str]:
    """Pending tasks that cannot run because an upstream task failed or blocked."""
    by_id = {task["id"]: task for task in task_records}
    visiting: set[str] = set()
    memo: dict[str, bool] = {}

    def is_unreachable(task_id: str) -> bool:
        if task_id in memo:
            return memo[task_id]
        if task_id in visiting:
            raise TaskCenterInvariantViolation(
                f"Generator task dependency cycle reached persisted task {task_id!r}"
            )
        if statuses[task_id] != TaskCenterTaskStatus.PENDING:
            memo[task_id] = False
            return False

        visiting.add(task_id)
        try:
            for dep_id in by_id[task_id].get("needs") or ():
                dep_status = statuses[dep_id]
                if dep_status in _FAILED_OR_BLOCKED:
                    memo[task_id] = True
                    return True
                if dep_status == TaskCenterTaskStatus.PENDING and is_unreachable(dep_id):
                    memo[task_id] = True
                    return True
            memo[task_id] = False
            return False
        finally:
            visiting.remove(task_id)

    return frozenset(
        task_id
        for task_id, status in statuses.items()
        if status == TaskCenterTaskStatus.PENDING and is_unreachable(task_id)
    )


def summarize_generator_dag(task_records: list[TaskRow]) -> GeneratorDagSummary:
    """Single-pass summary of generator statuses for DAG dispatch.

    A pending task whose dependency chain contains a FAILED or BLOCKED task is
    quiescent but not done: it is not-started work that can never become ready
    in this attempt.
    """
    status_map = _task_statuses_by_id(task_records)
    _validate_persisted_deps(task_records, status_map)
    unreachable_pending = _unreachable_pending_ids(task_records, status_map)
    statuses = status_map.values()
    return GeneratorDagSummary(
        all_quiescent=all(
            status in TERMINAL_GENERATOR_STATUSES
            or (status == TaskCenterTaskStatus.PENDING and task_id in unreachable_pending)
            for task_id, status in status_map.items()
        ),
        all_done=all(s == TaskCenterTaskStatus.DONE for s in statuses),
        any_failed_or_blocked=any(s in _FAILED_OR_BLOCKED for s in statuses),
    )
