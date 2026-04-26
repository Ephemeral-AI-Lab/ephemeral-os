"""Harness-graph readiness check used by the dispatcher to promote evaluators."""

from __future__ import annotations

from task_center.graph.store import TaskGraph
from task_center.model import HarnessGraphId, Status


_TERMINAL_STATUSES: frozenset[Status] = frozenset({Status.DONE, Status.FAILED})


def is_harness_graph_ready_for_evaluation(
    graph: TaskGraph, graph_id: HarnessGraphId
) -> bool:
    """True iff every executor in the harness graph is terminal and an evaluator exists."""
    harness = graph.get_harness_graph(graph_id)
    if harness.evaluator_task_id is None:
        return False
    for tid in harness.executor_task_ids:
        if graph.get(tid).status not in _TERMINAL_STATUSES:
            return False
    return True
