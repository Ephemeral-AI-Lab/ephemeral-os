"""Persistence Protocols at the TaskCenter boundary.

These are the narrow store contracts that ``task_center`` actually consumes.
Concrete implementations live in ``db.stores.*`` but task_center modules
depend only on these protocols, so:

- Tests can substitute in-memory or fake stores without monkey-patching
  ``db.stores`` module paths.
- The store contract can evolve independently of one implementation.
- Adding a second persistence backend (e.g. a Redis cache layer) does not
  require changes in ``task_center`` code.

Each protocol lists ONLY the methods task_center calls. Unused methods on
the concrete store classes (analytics queries, admin helpers) are out of
scope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
    Iteration,
    IterationCreationReason,
    IterationStatus,
    Workflow,
    WorkflowStatus,
)

# Row dicts returned by the task store. Always a serialized snapshot, never
# a live ORM row.
TaskRow = dict[str, Any]


class WorkflowStoreProtocol(Protocol):
    """Narrow contract for the workflow persistence surface."""

    is_ready: bool

    def insert(
        self,
        *,
        task_center_run_id: str,
        parent_task_id: str | None,
        workflow_goal: str,
    ) -> Workflow: ...

    def get(self, workflow_id: str) -> Workflow | None: ...

    def append_iteration_id(self, workflow_id: str, iteration_id: str) -> Workflow: ...

    def set_status(
        self,
        workflow_id: str,
        *,
        status: WorkflowStatus,
        closed_at: datetime | None,
    ) -> Workflow: ...

    def list_for_parent_task(self, parent_task_id: str) -> list[Workflow]: ...


class IterationStoreProtocol(Protocol):
    """Narrow contract for the iteration persistence surface."""

    is_ready: bool

    def insert(
        self,
        *,
        workflow_id: str,
        sequence_no: int,
        creation_reason: IterationCreationReason,
        iteration_goal: str,
        attempt_budget: int,
    ) -> Iteration: ...

    def get(self, iteration_id: str) -> Iteration | None: ...

    def append_attempt_id(self, iteration_id: str, attempt_id: str) -> Iteration: ...

    def set_status(
        self,
        iteration_id: str,
        *,
        status: IterationStatus,
        closed_at: datetime | None,
        outcomes: str | None = ...,
    ) -> Iteration: ...

    def set_deferred_goal_for_next_iteration(
        self, iteration_id: str, *, deferred_goal_for_next_iteration: str | None
    ) -> Iteration: ...

    def close_succeeded(
        self,
        iteration_id: str,
        *,
        outcomes: str,
        closed_at: datetime | None = None,
    ) -> Iteration: ...

    def list_for_workflow(self, workflow_id: str) -> list[Iteration]: ...


class AttemptStoreProtocol(Protocol):
    """Narrow contract for the attempt persistence surface."""

    is_ready: bool

    def insert(self, *, iteration_id: str, attempt_sequence_no: int) -> Attempt: ...

    def get(self, attempt_id: str) -> Attempt | None: ...

    def set_stage(self, attempt_id: str, stage: AttemptStage) -> Attempt: ...

    def set_planner_task_id(self, attempt_id: str, planner_task_id: str) -> Attempt: ...

    def set_generator_task_ids(self, attempt_id: str, generator_task_ids: list[str]) -> Attempt: ...

    def set_reducer_task_ids(self, attempt_id: str, reducer_task_ids: list[str]) -> Attempt: ...

    def set_deferred_goal(
        self,
        attempt_id: str,
        *,
        deferred_goal_for_next_iteration: str | None,
    ) -> Attempt: ...

    def close(
        self,
        attempt_id: str,
        *,
        status: AttemptStatus,
        fail_reason: AttemptFailReason | None,
        closed_at: datetime,
    ) -> Attempt: ...

    def list_for_iteration(self, iteration_id: str) -> list[Attempt]: ...


class TaskStoreProtocol(Protocol):
    """Narrow contract for the task-center task/run persistence surface."""

    is_ready: bool

    def create_request(
        self,
        *,
        request_id: str,
        cwd: str,
        sandbox_id: str | None,
        request_prompt: str,
    ) -> None: ...

    def create_run(self, *, task_center_run_id: str, request_id: str) -> None: ...

    def get_run(self, task_center_run_id: str) -> TaskRow | None: ...

    def finish_run(self, task_center_run_id: str, *, status: str) -> None: ...

    def upsert_task(
        self,
        *,
        task_id: str,
        task_center_run_id: str,
        role: str,
        context_message: str,
        status: str,
        outcomes: list[Any],
        needs: list[str],
        agent_name: str | None = ...,
        terminal_tool_result: dict[str, Any] | None = ...,
        child_workflow_id: str | None = ...,
    ) -> None: ...

    def get_task(self, task_id: str) -> TaskRow | None: ...

    def set_task_status(
        self,
        task_id: str,
        *,
        status: str,
        outcomes: list[Any] | None = ...,
        terminal_tool_result: dict[str, Any] | None = ...,
    ) -> TaskRow: ...

    def set_task_status_if_current(
        self,
        task_id: str,
        *,
        expected_status: str,
        status: str,
        outcomes: list[Any] | None = ...,
        terminal_tool_result: dict[str, Any] | None = ...,
        child_workflow_id: str | None = ...,
    ) -> TaskRow | None: ...


__all__ = [
    "WorkflowStoreProtocol",
    "IterationStoreProtocol",
    "AttemptStoreProtocol",
    "TaskStoreProtocol",
    "TaskRow",
]
