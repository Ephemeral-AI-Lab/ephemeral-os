"""Plan DAG helpers for one attempt — the planner-authored generator+reducer graph.

Named for the *plan* (not every task is in it: planner/advisor/explorer are
off-spine). ``ordered_plan_tasks`` validates the combined generator+reducer DAG
and enforces the two structural rules that keep "every attempt has an exit AND
all work is judged" by construction: **≥1 reducer**, and **reachability** —
every generator transitively needed by ≥1 reducer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from task_center._core.persistence import TaskRow
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center._core.task_state import (
    TERMINAL_GENERATOR_STATUSES,
    TaskCenterTaskStatus,
)
from task_center.submissions import PlannedGeneratorTask, PlannedReducerTask


def ordered_plan_tasks(
    generators: tuple[PlannedGeneratorTask, ...],
    reducers: tuple[PlannedReducerTask, ...],
) -> tuple[tuple[PlannedGeneratorTask, ...], tuple[PlannedReducerTask, ...]]:
    """Validate the combined plan DAG and return both tuples in topo order.

    Raises :class:`TaskCenterInvariantViolation` on a duplicate local id, an
    unknown ``needs`` target, a dependency cycle, an empty reducer set, or an
    unreachable generator (one no reducer transitively needs).
    """
    by_needs: dict[str, tuple[str, ...]] = {}
    duplicates: list[str] = []
    for task in (*generators, *reducers):
        if task.local_id in by_needs:
            duplicates.append(task.local_id)
        else:
            by_needs[task.local_id] = task.needs
    if duplicates:
        raise TaskCenterInvariantViolation(
            f"Plan contains duplicate local ids: {tuple(duplicates)!r}"
        )

    for local_id, needs in by_needs.items():
        missing = [dep for dep in needs if dep not in by_needs]
        if missing:
            raise TaskCenterInvariantViolation(
                f"Plan task {local_id!r} has unknown needs: {missing!r}"
            )

    _assert_acyclic(by_needs)

    if not reducers:
        raise TaskCenterInvariantViolation("Plan must contain at least one reducer")

    reachable = _needs_closure(roots=[r.local_id for r in reducers], by_needs=by_needs)
    unreachable = [g.local_id for g in generators if g.local_id not in reachable]
    if unreachable:
        raise TaskCenterInvariantViolation(
            f"Plan has generator(s) no reducer needs: {tuple(unreachable)!r}"
        )

    order = _topo_order(by_needs)
    rank = {local_id: i for i, local_id in enumerate(order)}
    ordered_gen = tuple(sorted(generators, key=lambda g: rank[g.local_id]))
    ordered_red = tuple(sorted(reducers, key=lambda r: rank[r.local_id]))
    return ordered_gen, ordered_red


def _assert_acyclic(by_needs: dict[str, tuple[str, ...]]) -> None:
    if len(_topo_order(by_needs)) != len(by_needs):
        ordered = set(_topo_order(by_needs))
        cycle = tuple(local_id for local_id in by_needs if local_id not in ordered)
        raise TaskCenterInvariantViolation(
            f"Plan contains a dependency cycle among: {cycle!r}"
        )


def _topo_order(by_needs: dict[str, tuple[str, ...]]) -> list[str]:
    remaining = {local_id: set(needs) for local_id, needs in by_needs.items()}
    dependents: dict[str, list[str]] = {local_id: [] for local_id in by_needs}
    for local_id, needs in by_needs.items():
        for dep in needs:
            dependents[dep].append(local_id)
    ready = deque(local_id for local_id, needs in remaining.items() if not needs)
    order: list[str] = []
    while ready:
        local_id = ready.popleft()
        order.append(local_id)
        for dependent in dependents[local_id]:
            remaining[dependent].discard(local_id)
            if not remaining[dependent]:
                ready.append(dependent)
    return order


def _needs_closure(*, roots: list[str], by_needs: dict[str, tuple[str, ...]]) -> set[str]:
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        local_id = stack.pop()
        for dep in by_needs.get(local_id, ()):
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def _task_statuses_by_id(
    task_records: list[TaskRow],
) -> dict[str, TaskCenterTaskStatus]:
    return {task["task_id"]: TaskCenterTaskStatus(task["status"]) for task in task_records}


def ready_pending_plan_ids(task_records: list[TaskRow]) -> tuple[str, ...]:
    """Pending plan tasks whose ``needs`` are all DONE — ready to launch."""
    statuses = _task_statuses_by_id(task_records)
    _validate_persisted_needs(task_records, statuses)
    ready: list[str] = []
    for task in task_records:
        if statuses[task["task_id"]] != TaskCenterTaskStatus.PENDING:
            continue
        needs = tuple(task.get("needs") or ())
        if all(statuses[dep] == TaskCenterTaskStatus.DONE for dep in needs):
            ready.append(task["task_id"])
    return tuple(ready)


@dataclass(frozen=True, slots=True)
class DagStatus:
    all_quiescent: bool
    all_done: bool
    any_failed_or_blocked: bool


_FAILED_OR_BLOCKED = (TaskCenterTaskStatus.FAILED, TaskCenterTaskStatus.BLOCKED)


def _validate_persisted_needs(
    task_records: list[TaskRow],
    statuses: dict[str, TaskCenterTaskStatus],
) -> None:
    for task in task_records:
        missing = [dep for dep in task.get("needs") or () if dep not in statuses]
        if missing:
            raise TaskCenterInvariantViolation(
                f"Plan task {task['task_id']!r} has unknown persisted needs: {missing!r}"
            )


def _unreachable_pending_ids(
    task_records: list[TaskRow],
    statuses: dict[str, TaskCenterTaskStatus],
) -> frozenset[str]:
    """Pending tasks that cannot run because an upstream task failed or blocked."""
    by_id = {task["task_id"]: task for task in task_records}
    visiting: set[str] = set()
    memo: dict[str, bool] = {}

    def is_unreachable(task_id: str) -> bool:
        if task_id in memo:
            return memo[task_id]
        if task_id in visiting:
            raise TaskCenterInvariantViolation(
                f"Plan task dependency cycle reached persisted task {task_id!r}"
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


def dag_status(task_records: list[TaskRow]) -> DagStatus:
    """Single-pass summary of plan-task statuses for DAG dispatch.

    A pending task whose dependency chain contains a FAILED or BLOCKED task is
    quiescent but not done: it is not-started work that can never become ready
    in this attempt.
    """
    status_map = _task_statuses_by_id(task_records)
    _validate_persisted_needs(task_records, status_map)
    unreachable_pending = _unreachable_pending_ids(task_records, status_map)
    statuses = status_map.values()
    return DagStatus(
        all_quiescent=all(
            status in TERMINAL_GENERATOR_STATUSES
            or (status == TaskCenterTaskStatus.PENDING and task_id in unreachable_pending)
            for task_id, status in status_map.items()
        ),
        all_done=all(s == TaskCenterTaskStatus.DONE for s in statuses),
        any_failed_or_blocked=any(s in _FAILED_OR_BLOCKED for s in statuses),
    )
