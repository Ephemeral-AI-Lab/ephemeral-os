"""Pure read-only queries over a TaskGraph.

These were lifted off ``TaskCenter`` because they only need the graph itself —
no orchestrator state. Keeping them as free functions makes them trivially
testable against a hand-built ``TaskGraph`` fixture.
"""

from __future__ import annotations

from task_center.graph.store import TaskGraph
from task_center.model import Status, Task, TaskId


_TERMINAL_STATUSES: frozenset[Status] = frozenset({Status.DONE, Status.FAILED})


def dependency_blocked_descendants(graph: TaskGraph, task_id: TaskId) -> list[Task]:
    """Return non-terminal generator tasks whose dependency path now contains ``task_id``.

    Generators (executors and verifiers) cascade-fail together when an
    upstream dependency fails. Evaluators are excluded — they dispatch via
    harness graph readiness and must see FAILED sibling generators instead
    of being short-circuited.
    """
    out: list[Task] = []
    seen: set[TaskId] = set()
    frontier: list[TaskId] = [task_id]
    while frontier:
        current = frontier.pop()
        for candidate in graph.tasks.values():
            if candidate.id in seen or candidate.id == task_id:
                continue
            if candidate.role not in ("executor", "verifier"):
                continue
            if current in candidate.needs and candidate.status not in _TERMINAL_STATUSES:
                seen.add(candidate.id)
                out.append(candidate)
                frontier.append(candidate.id)
    return out
