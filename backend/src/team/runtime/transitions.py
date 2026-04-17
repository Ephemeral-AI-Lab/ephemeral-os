"""TransitionTracker — task state-change diff and emission.

Extracted from TaskCenter. Snapshots task signatures, refreshes the graph
from the store, and emits make_task_status events for every changed task.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from team.models import Task
from team.persistence.events import TeamRunEvent, make_task_status

logger = logging.getLogger(__name__)


def iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def task_status_payload(task: Task) -> dict[str, Any]:
    return {
        "agent_run_id": task.agent_run_id,
        "started_at": iso(task.started_at),
        "finished_at": iso(task.finished_at),
        "failure_reason": task.failure_reason,
    }


def task_state_signature(task: Task | None) -> tuple[Any, ...] | None:
    if task is None:
        return None
    return (
        task.status.value,
        task.agent_run_id,
        iso(task.started_at),
        iso(task.finished_at),
        task.failure_reason,
    )


class TransitionTracker:
    """Snapshot/diff/emit task state transitions against a store-backed graph."""

    def __init__(
        self,
        *,
        team_run_id: str,
        graph_getter: Callable[[], dict[str, Task]],
        refresh_graph_fn: Callable[[], Awaitable[Any]],
        emit_cb: Callable[[TeamRunEvent], None],
    ) -> None:
        self._team_run_id = team_run_id
        self._graph_getter = graph_getter
        self._refresh_graph_fn = refresh_graph_fn
        self._emit = emit_cb

    def snapshot(
        self, task_ids: set[str] | None = None
    ) -> dict[str, tuple[Any, ...] | None]:
        graph = self._graph_getter()
        ids = task_ids if task_ids is not None else set(graph)
        return {tid: task_state_signature(graph.get(tid)) for tid in ids}

    async def refresh_and_emit(
        self, before: dict[str, tuple[Any, ...] | None]
    ) -> None:
        await self._refresh_graph_fn()
        graph = self._graph_getter()
        for tid, prior in before.items():
            task = graph.get(tid)
            if task is None:
                continue
            current = task_state_signature(task)
            if current == prior:
                continue
            self._emit(
                make_task_status(
                    self._team_run_id,
                    task.id,
                    task.status.value,
                    **task_status_payload(task),
                )
            )

    def emit_full_status(self, task: Task) -> None:
        self._emit(
            make_task_status(
                self._team_run_id,
                task.id,
                task.status.value,
                **task_status_payload(task),
            )
        )

    def emit_status(self, task_id: str, status: str, **payload: Any) -> None:
        """Emit a sparse task_status event with caller-specified payload fields."""
        self._emit(make_task_status(self._team_run_id, task_id, status, **payload))
