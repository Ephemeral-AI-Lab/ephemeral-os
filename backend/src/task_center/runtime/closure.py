"""Harness graph closure helpers.

Planner-led graphs close through their final verifier. A full-plan verifier
success marks the graph's root task done; a partial-plan verifier success
spawns the continuation graph. Failures propagate through dependency edges and
close the owning graph when the final verifier is failed or blocked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.graph import dependency_blocked_descendants
from task_center.model import HarnessGraphId, Status, Task, TaskId, TaskSummary
from task_center.summaries import latest_summary_text

if TYPE_CHECKING:
    from task_center.runtime.task_center import TaskCenter


_TERMINAL_STATUSES: frozenset[Status] = frozenset({Status.DONE, Status.FAILED})


def terminal_verifier_id(
    tc: "TaskCenter", graph_id: HarnessGraphId
) -> TaskId | None:
    """Return the graph's single final verifier if the graph has one."""
    graph = tc.graph.get_harness_graph(graph_id)
    if not graph.dag_nodes:
        return None

    node_ids = set(graph.dag_nodes)
    depended_upon: set[TaskId] = set()
    for nid in graph.dag_nodes:
        depended_upon.update(tc.graph.get(nid).needs & node_ids)
    sinks = [nid for nid in graph.dag_nodes if nid not in depended_upon]
    if len(sinks) != 1:
        return None

    candidate = tc.graph.get(sinks[0])
    if candidate.role != "verifier":
        return None
    if candidate.needs != frozenset(node_ids - {candidate.id}):
        return None
    return candidate.id


def is_terminal_verifier(
    tc: "TaskCenter", graph_id: HarnessGraphId, task_id: TaskId
) -> bool:
    """True when ``task_id`` is the graph-closing verifier."""
    return terminal_verifier_id(tc, graph_id) == task_id


def close_if_terminal_verifier_failed(
    tc: "TaskCenter", graph_id: HarnessGraphId
) -> None:
    """Close ``graph_id`` as failed if its final verifier is already failed."""
    verifier_id = terminal_verifier_id(tc, graph_id)
    if verifier_id is None:
        return
    verifier = tc.graph.get(verifier_id)
    if verifier.status is Status.FAILED:
        close_harness_graph_failed(tc, graph_id, verifier_id)


def close_harness_graph_success(
    tc: "TaskCenter", graph_id: HarnessGraphId, source_task_id: TaskId
) -> None:
    """Close a harness graph successfully and propagate to its root task."""
    graph = tc.graph.get_harness_graph(graph_id)
    planner = tc.graph.get(graph.planner_task_id)
    parent = tc.graph.get(graph.root_task_id)
    if planner.status in _TERMINAL_STATUSES and parent.status in _TERMINAL_STATUSES:
        return

    tc._mark_terminal(planner, Status.DONE)
    source_task = tc.graph.get(source_task_id)
    parent.summaries.append(
        TaskSummary(
            kind="child_success",
            text=latest_summary_text(source_task) or "",
            source_task_id=source_task_id,
        )
    )
    tc._mark_terminal(parent, Status.DONE)
    propagate_parent_terminal(tc, parent, success=True)


def close_harness_graph_failed(
    tc: "TaskCenter", graph_id: HarnessGraphId, source_task_id: TaskId
) -> None:
    """Close a harness graph as failed and propagate to its root task."""
    graph = tc.graph.get_harness_graph(graph_id)
    planner = tc.graph.get(graph.planner_task_id)
    parent = tc.graph.get(graph.root_task_id)
    if planner.status in _TERMINAL_STATUSES and parent.status in _TERMINAL_STATUSES:
        return

    tc._mark_terminal(planner, Status.FAILED)
    source_task = tc.graph.get(source_task_id)
    parent.summaries.append(
        TaskSummary(
            kind="child_failure",
            text=latest_summary_text(source_task) or "",
            source_task_id=source_task_id,
        )
    )
    tc._mark_terminal(parent, Status.FAILED)
    propagate_parent_terminal(tc, parent, success=False)


def propagate_parent_terminal(
    tc: "TaskCenter", parent: Task, *, success: bool
) -> None:
    """Bubble a graph root's terminal state across enclosing graph boundaries."""
    if parent.task_center_harness_graph_id is None:
        return
    if success:
        tc._notify_child_terminal_changed()
        return

    for descendant in dependency_blocked_descendants(tc.graph, parent.id):
        descendant.summaries.append(
            TaskSummary(
                kind="dependency_blocked",
                text=f"Blocked because dependency {parent.id!r} failed.",
                source_task_id=parent.id,
            )
        )
        tc._mark_terminal(descendant, Status.FAILED)
    close_if_terminal_verifier_failed(tc, parent.task_center_harness_graph_id)
    tc._notify_child_terminal_changed()
