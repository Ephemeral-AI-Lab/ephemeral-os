"""Pure read-only queries over a TaskGraph.

These were lifted off ``TaskCenter`` because they only need the graph itself —
no orchestrator state. Keeping them as free functions makes them trivially
testable against a hand-built ``TaskGraph`` fixture.
"""

from __future__ import annotations

from task_center.graph.store import TaskGraph
from task_center.model import Status, Task, TaskId, TaskSummary


_TERMINAL_STATUSES: frozenset[Status] = frozenset({Status.DONE, Status.FAILED})


def parent_goal(graph: TaskGraph, task_id: TaskId) -> str | None:
    """Return the goal of the harness graph that owns ``task_id``."""
    task = graph.get(task_id)
    if task.task_center_harness_graph_id is None:
        return None
    harness = graph.get_harness_graph(task.task_center_harness_graph_id)
    return graph.get(harness.parent_task_id).input


def planner_handoff(graph: TaskGraph, task_id: TaskId) -> list[TaskSummary]:
    """Return the planner's handoff summaries for the harness graph owning ``task_id``."""
    task = graph.get(task_id)
    if task.task_center_harness_graph_id is None:
        return []
    harness = graph.get_harness_graph(task.task_center_harness_graph_id)
    planner = graph.get(harness.planner_task_id)
    return [s for s in planner.summaries if s.kind == "handoff"]


def dependency_blocked_descendants(graph: TaskGraph, task_id: TaskId) -> list[Task]:
    """Return non-terminal executor tasks whose dependency path now contains ``task_id``.

    Evaluators are excluded — they dispatch via harness graph readiness and
    must see FAILED sibling executors instead of being short-circuited.
    """
    out: list[Task] = []
    seen: set[TaskId] = set()
    frontier: list[TaskId] = [task_id]
    while frontier:
        current = frontier.pop()
        for candidate in graph.tasks.values():
            if candidate.id in seen or candidate.id == task_id:
                continue
            if candidate.role != "executor":
                continue
            if current in candidate.needs and candidate.status not in _TERMINAL_STATUSES:
                seen.add(candidate.id)
                out.append(candidate)
                frontier.append(candidate.id)
    return out
