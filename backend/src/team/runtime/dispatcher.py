"""Dispatcher — DAG, ready queue, and atomic mutations for one TeamRun."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from team.errors import (
    BudgetExceeded,
    InvalidPlan,
)
from team.models import (
    AgentResult,
    BudgetConfig,
    BudgetState,
    ReplanRequest,
    RetryRequest,
    Task,
    TaskSpec,
    TaskStatus,
    TERMINAL_STATUSES,
    _utcnow,
)
from team.persistence.events import (
    TeamRunEvent,
    make_budget_update,
    make_work_item_added,
    make_work_item_status,
    work_item_to_dict,
)
from team.persistence.run_store import NullTeamRunStore, TeamRunStore
from team.planning.validation import validate_plan
from team.runtime.dispatcher_checkpoint_ops import (
    checkpoint as checkpoint_dispatcher_state,
    prepare_for_resume as prepare_dispatcher_for_resume,
    rollback_to as rollback_dispatcher_state,
)
from team.runtime.dispatcher_mutation_ops import (
    cancel_all_pending as cancel_dispatcher_pending,
    cancel_running as cancel_dispatcher_running,
    cascade_cancel_dependency_subtree,
    fail as fail_work_item,
    retry_work_item as retry_dispatcher_work_item,
)
from team.runtime.dispatcher_replan_ops import (
    request_replan as request_dispatcher_replan,
)
from team.runtime.checkpoint import TeamRunCheckpoint


class Dispatcher:
    """Owns the Task DAG for one TeamRun. Mutations are lock-protected."""

    def __init__(
        self,
        team_run_id: str,
        budgets: BudgetConfig,
        budget_state: BudgetState,
        max_checkpoints: int = 10,
        event_store: TeamRunStore | None = None,
    ) -> None:
        self.team_run_id = team_run_id
        self.budgets = budgets
        self.budget_state = budget_state
        self.graph: dict[str, Task] = {}
        self._ready_queue: asyncio.Queue[str] = asyncio.Queue()
        self._ready_order: list[str] = []
        self.lock = asyncio.Lock()
        self._checkpoints: deque[TeamRunCheckpoint] = deque(maxlen=max_checkpoints)
        self._checkpoint_seq = 0
        self._events: TeamRunStore = event_store or NullTeamRunStore()
        # Set by TeamRun after construction so cascade "continue" can inject notes
        self.task_center: Any = None

    # ---- event emission --------------------------------------------------

    def _emit(self, event: TeamRunEvent) -> None:
        """Append an event to the durable store.

        Called *only* while ``self.lock`` is held so per-run ordering
        matches the in-memory state machine. The store is expected to be
        cheap (NullTeamRunStore is free; JsonlTeamRunStore is one fsync).
        """
        try:
            self._events.append(event)
        except Exception:  # pragma: no cover — don't let persistence kill the run
            import logging
            logging.getLogger(__name__).exception(
                "team event store append failed; continuing in-memory"
            )

    def _emit_budget(self) -> None:
        self._emit(
            make_budget_update(
                self.team_run_id,
                tasks_used=self.budget_state.tasks_used,
                note_bytes_used=self.budget_state.note_bytes_used,
                replans_used=self.budget_state.replans_used,
            )
        )

    def new_id(self) -> str:
        return str(uuid.uuid4())

    def _mark_failed(self, wi: Task, reason: str) -> None:
        wi.status = TaskStatus.FAILED
        wi.finished_at = _utcnow()
        wi.failure_reason = reason
        self._emit(
            make_work_item_status(
                self.team_run_id,
                wi.id,
                "failed",
                finished_at=wi.finished_at.isoformat() if wi.finished_at else None,
                failure_reason=wi.failure_reason,
            )
        )

    def _mark_cancelled(self, wi: Task, reason: str) -> None:
        wi.status = TaskStatus.CANCELLED
        wi.finished_at = _utcnow()
        wi.failure_reason = reason
        self._emit(
            make_work_item_status(
                self.team_run_id,
                wi.id,
                "cancelled",
                finished_at=wi.finished_at.isoformat(),
                failure_reason=wi.failure_reason,
            )
        )

    def _compute_readiness(self, wi: Task) -> bool:
        """A Task becomes READY iff PENDING and all dependency subtrees resolve."""
        if wi.status != TaskStatus.PENDING:
            return False
        for dep_id in wi.deps:
            if not self._dependency_satisfied(dep_id):
                return False
        return True

    def _ancestor_ids(self, wi_id: str) -> list[str]:
        ancestors: list[str] = []
        seen: set[str] = set()
        current = self.graph.get(wi_id)
        while current is not None and current.parent_id:
            parent_id = current.parent_id
            if parent_id in seen:
                break
            ancestors.append(parent_id)
            seen.add(parent_id)
            current = self.graph.get(parent_id)
        return ancestors

    def _dependency_root_ids(self, wi_id: str) -> list[str]:
        return [wi_id, *self._ancestor_ids(wi_id)]

    def _subtree_ids(self, root_id: str) -> list[str]:
        ordered: list[str] = []
        stack = [root_id]
        seen: set[str] = set()
        while stack:
            current_id = stack.pop()
            if current_id in seen:
                continue
            seen.add(current_id)
            ordered.append(current_id)
            child_ids = [
                child.id for child in self.graph.values() if child.parent_id == current_id
            ]
            stack.extend(reversed(child_ids))
        return ordered

    def _dependency_satisfied(self, dep_id: str) -> bool:
        dep = self.graph.get(dep_id)
        if dep is None or dep.status != TaskStatus.DONE:
            return False
        for node_id in self._subtree_ids(dep_id):
            node = self.graph.get(node_id)
            if node is None:
                return False
            if node.status == TaskStatus.FAILED:
                return False
            if node.status not in (TaskStatus.DONE, TaskStatus.CANCELLED):
                return False
        return True

    def _cancel_superseded_dependency_validators(self, wi: Task) -> None:
        from agents.registry import has_role

        if not has_role(wi.agent_name, "reviewer") or wi.status not in (
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.RUNNING,
        ):
            return
        for node_id in {node for dep_id in wi.deps for node in self._subtree_ids(dep_id)}:
            node = self.graph.get(node_id)
            if node_id != wi.id and node and has_role(node.agent_name, "reviewer") and node.status == TaskStatus.FAILED:
                self._mark_cancelled(node, f"superseded_by_active_validator_{wi.id}")

    def _promote_ready_work_items(self) -> None:
        for candidate in list(self.graph.values()):
            self._cancel_superseded_dependency_validators(candidate)
            if self._compute_readiness(candidate):
                self._promote_to_ready(candidate)

    def _enqueue(self, wi: Task) -> None:
        wi.status = TaskStatus.READY
        self._ready_queue.put_nowait(wi.id)
        self._ready_order.append(wi.id)
        self._emit(make_work_item_status(self.team_run_id, wi.id, "ready"))

    def _promote_to_ready(self, wi: Task) -> None:
        """Single chokepoint for PENDING→READY: enqueues the work item."""
        assert wi.status == TaskStatus.PENDING, (
            f"_promote_to_ready called on {wi.id} in status {wi.status.value}"
        )
        self._enqueue(wi)

    async def add_work_item(self, wi: Task) -> None:
        async with self.lock:
            if self.budget_state.tasks_used >= self.budgets.max_tasks:
                raise BudgetExceeded(
                    f"max_tasks={self.budgets.max_tasks} reached"
                )
            if wi.id in self.graph:
                raise ValueError(f"Task {wi.id} already exists")
            self.graph[wi.id] = wi
            self.budget_state.tasks_used += 1
            self._emit(make_work_item_added(self.team_run_id, work_item_to_dict(wi)))
            self._emit_budget()
            if self._compute_readiness(wi):
                self._promote_to_ready(wi)

    async def pop_ready(self) -> str:
        while True:
            wi_id = await self._ready_queue.get()
            async with self.lock:
                try:
                    self._ready_order.remove(wi_id)
                except ValueError:
                    pass
                wi = self.graph.get(wi_id)
                if wi is None or wi.status != TaskStatus.READY:
                    continue
                return wi_id

    async def mark_running(self, wi_id: str, agent_run_id: str) -> Task:
        async with self.lock:
            wi = self.graph[wi_id]
            if wi.status != TaskStatus.READY:
                raise RuntimeError(
                    f"mark_running: {wi_id} is {wi.status.value}, not READY"
                )
            wi.status = TaskStatus.RUNNING
            wi.agent_run_id = agent_run_id
            wi.started_at = _utcnow()
            self._emit(
                make_work_item_status(
                    self.team_run_id,
                    wi_id,
                    "running",
                    agent_run_id=agent_run_id,
                    started_at=wi.started_at.isoformat(),
                )
            )
            return wi

    async def complete(self, wi_id: str, result: AgentResult) -> list[Task]:
        """Mark DONE and atomically insert any submitted Plan."""
        new_items: list[Task] = []
        async with self.lock:
            wi = self.graph[wi_id]
            if wi.status != TaskStatus.RUNNING:
                raise RuntimeError(
                    f"complete: {wi_id} is {wi.status.value}, not RUNNING"
                )

            from agents.registry import has_role as _has_role_check
            if _has_role_check(wi.agent_name, "planner") and result.submitted_plan is None:
                self._mark_failed(
                    wi,
                    "InvalidPlan: expandable work item did not submit a plan",
                )
                cascade_cancel_dependency_subtree(self, wi_id)
                return []

            if result.submitted_plan is not None:
                new_depth = wi.depth + 1
                if new_depth > self.budgets.max_depth:
                    self._mark_failed(
                        wi,
                        f"InvalidPlan: plan would exceed max_depth={self.budgets.max_depth}",
                    )
                    cascade_cancel_dependency_subtree(self, wi_id)
                    return []
                issues = validate_plan(
                    result.submitted_plan,
                    max_plan_size=self.budgets.max_plan_size,
                    known_external_deps=set(self.graph.keys()),
                )
                if issues:
                    self._mark_failed(
                        wi,
                        "InvalidPlan: " + "; ".join(i["msg"] for i in issues),
                    )
                    cascade_cancel_dependency_subtree(self, wi_id)
                    return []
                # Build Task objects from TaskSpec, resolving local ids → global ids.
                local_to_global: dict[str, str] = {
                    spec.id: self.new_id()
                    for spec in result.submitted_plan.tasks
                    if spec.id
                }
                for spec in result.submitted_plan.tasks:
                    new_id = local_to_global.get(spec.id) or self.new_id()
                    resolved_deps: list[str] = [
                        local_to_global[d] if d in local_to_global else d
                        for d in spec.deps
                    ]
                    new_items.append(
                        Task(
                            id=new_id,
                            team_run_id=self.team_run_id,
                            agent_name=spec.agent,
                            status=TaskStatus.PENDING,
                            task=spec.task,
                            deps=resolved_deps,
                            scope_paths=list(spec.scope_paths),
                            cascade_policy=spec.cascade_policy,
                            parent_id=wi.id,
                            root_id=wi.root_id or wi.id,
                            depth=new_depth,
                        )
                    )
                if (
                    self.budget_state.tasks_used + len(new_items)
                    > self.budgets.max_tasks
                ):
                    self._mark_failed(wi, "BudgetExceeded: max_tasks")
                    cascade_cancel_dependency_subtree(self, wi_id)
                    return []

            for nwi in new_items:
                self.graph[nwi.id] = nwi
                self.budget_state.tasks_used += 1
                self._emit(make_work_item_added(self.team_run_id, work_item_to_dict(nwi)))
            if new_items:
                self._emit_budget()

            wi.status = TaskStatus.DONE
            wi.finished_at = _utcnow()
            self._emit(
                make_work_item_status(
                    self.team_run_id,
                    wi_id,
                    "done",
                    finished_at=wi.finished_at.isoformat(),
                )
            )

            self._promote_ready_work_items()

            # Apply replan inside the same lock acquisition to prevent a race
            # where a newly-promoted task gets dequeued between lock release
            # and apply_replan's re-acquisition.
            if result.submitted_replan is not None:
                from team.runtime.dispatcher_replan_ops import apply_replan_unlocked
                apply_replan_unlocked(
                    self,
                    replan_task_id=wi_id,
                    add_tasks=result.submitted_replan.add_tasks,
                    cancel_ids=result.submitted_replan.cancel_ids,
                    target_depth=wi.depth,
                    target_parent_id=wi.parent_id,
                    target_root_id=wi.root_id,
                )

        return new_items

    async def fail(self, wi_id: str, reason: str) -> None:
        await fail_work_item(self, wi_id=wi_id, reason=reason)

    # ---- retry / replan --------------------------------------------------

    async def retry_work_item(self, wi_id: str, request: RetryRequest) -> None:
        """Reset a RUNNING work item back to READY for re-execution."""
        await retry_dispatcher_work_item(self, wi_id=wi_id, request=request)

    async def request_replan(self, wi_id: str, request: ReplanRequest) -> Task:
        """Fail the work item and spawn an ATOMIC replanner at the same depth level."""
        return await request_dispatcher_replan(
            self,
            wi_id=wi_id,
            request=request,
        )

    async def cancel_all_pending(self) -> None:
        await cancel_dispatcher_pending(self)

    async def cancel_running(self, reason: str) -> None:
        """Mark any RUNNING items as CANCELLED. Used after a cooperative drain."""
        await cancel_dispatcher_running(self, reason=reason)

    def all_terminal(self) -> bool:
        return all(wi.status in TERMINAL_STATUSES for wi in self.graph.values())

    # ---- checkpoint / rollback -------------------------------------------

    async def checkpoint(
        self,
        label: str | None,
        project_context: Any,
    ) -> TeamRunCheckpoint:
        return await checkpoint_dispatcher_state(
            self,
            label=label,
            project_context=project_context,
        )

    def list_checkpoints(self) -> list[TeamRunCheckpoint]:
        return list(self._checkpoints)

    def _get_checkpoint(self, checkpoint_id: str) -> TeamRunCheckpoint | None:
        return next((cp for cp in self._checkpoints if cp.id == checkpoint_id), None)

    async def rollback_to(
        self,
        checkpoint_id: str,
        project_context_setter: Callable[[Any], None],
    ) -> TeamRunCheckpoint:
        """Atomically restore graph + artifacts + context. Caller must drain workers first."""
        return await rollback_dispatcher_state(
            self,
            checkpoint_id=checkpoint_id,
            project_context_setter=project_context_setter,
        )

    async def prepare_for_resume(self) -> None:
        """Normalize live state after process loss and rebuild the ready queue."""
        await prepare_dispatcher_for_resume(self)

    # ---- replan: lateral DAG mutation ------------------------------------

    async def apply_replan(
        self,
        replan_task_id: str,
        add_tasks: list[TaskSpec],
        cancel_ids: list[str],
        target_depth: int,
        target_parent_id: str | None,
        target_root_id: str,
    ) -> dict[str, int]:
        """Atomically cancel stale items and insert corrective items at the target level."""
        from team.runtime.dispatcher_replan_ops import apply_replan_unlocked
        async with self.lock:
            return apply_replan_unlocked(
                self,
                replan_task_id=replan_task_id,
                add_tasks=add_tasks,
                cancel_ids=cancel_ids,
                target_depth=target_depth,
                target_parent_id=target_parent_id,
                target_root_id=target_root_id,
            )
