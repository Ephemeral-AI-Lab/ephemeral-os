"""TaskCoordinator — single owner of every task status change.

All graph mutation flows through one pattern:

    mutation = self._graph.<rule>(...)
    await self._store.persist(mutation)
    self._graph.apply(mutation)
    # emit events for the changes we care about

Outcome-driven transitions go through ``handle()`` (one match block, five
cases) under an asyncio lock so concurrent workers never interleave graph
mutations:

- ``DONE``              — mark done + cascade promotions
- ``EXPANDED``          — insert plan/replan children
- ``REQUEST_REPLAN``    — spawn recovery replanner
- ``CANCELLED``         — cascade cancel
- ``FAILED``            — mark failed + fail-fast the run

The atomic ``ready → running`` claim is exposed as ``claim_running()``; it is
the executor's intent-to-start handshake and also emits the ``running`` event.
Re-entry from inside a ``_dispatch`` case calls ``self._dispatch`` directly
(the lock is already held).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from team.core.errors import BudgetExceeded, GraphInvariantViolation, InvalidPlan
from team.core.models import (
    Plan,
    ReplanPlan,
    Task,
    TaskStatus,
    TaskStatusUpdate,
)
from team.task_center.budget import BudgetManager
from team.persistence.events import (
    TeamRunEvent,
    make_replace_dependency,
    make_task_added,
    make_task_status,
    task_to_dict,
)
from team.persistence.task_store import TaskStore
from team.planning.expander import PlanExpander
from team.runtime.task_graph import TaskGraph

if TYPE_CHECKING:
    from team.runtime.task_queue import TaskQueue


def _has_replanner_role(agent_name: str) -> bool:
    from agents.registry import get_role

    return get_role(agent_name) == "replanner"


class TaskCoordinator:
    """Single owner for every task status transition."""

    def __init__(
        self,
        *,
        team_run_id: str,
        graph: TaskGraph,
        store: TaskStore,
        budget: BudgetManager,
        expander: PlanExpander,
        emit_event: Callable[[TeamRunEvent], None],
        fail_fast: Callable[[str], Awaitable[None]],
        cancel_running_task: Callable[[str], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self._team_run_id = team_run_id
        self._graph = graph
        self._store = store
        self._budget = budget
        self._expander = expander
        self._emit = emit_event
        self._fail_fast = fail_fast
        self._cancel_running_task = cancel_running_task
        self._cancel_event = cancel_event
        self._queue: "TaskQueue | None" = None
        self._lock = asyncio.Lock()

    # ---- wiring ----------------------------------------------------------

    def bind_queue(self, queue: "TaskQueue") -> None:
        self._queue = queue

    # ---- public entry points --------------------------------------------

    async def claim_running(self, task_id: str, agent_run_id: str) -> Task | None:
        """Atomic ``ready → running`` claim for the executor.

        Lockless: ``store.mark_running`` is DB-atomic (the only carve-out
        from the in-memory-first pattern), and serializing every worker
        claim on the coordinator lock would bottleneck startup.
        """
        rec = await self._store.mark_running(task_id, agent_run_id)
        if rec is None:
            return None
        task = self._graph.get(task_id)
        if task is None:
            return None
        self._emit(
            make_task_status(
                self._team_run_id,
                task_id,
                "running",
                agent_run_id=agent_run_id,
                started_at=_iso(task.started_at),
            )
        )
        return task

    async def handle(self, update: TaskStatusUpdate) -> None:
        """Lock-serialized single dispatch entry."""
        async with self._lock:
            await self._dispatch(update)

    async def on_task_added(self, task: Task) -> None:
        async with self._lock:
            current = self._graph.get(task.id) or task
            if current.status == TaskStatus.READY:
                self._enqueue(current.id)

    # ---- dispatch (single match site) -----------------------------------

    async def _dispatch(self, update: TaskStatusUpdate) -> None:
        status = update.status
        if status is TaskStatus.DONE:
            await self._on_success(update)
        elif status is TaskStatus.EXPANDED:
            await self._on_expanded(update)
        elif status is TaskStatus.REQUEST_REPLAN:
            await self._on_request_replan(update)
        elif status is TaskStatus.CANCELLED:
            await self._on_cancelled(update)
        elif status is TaskStatus.FAILED:
            await self._on_failed(update)
        else:
            raise ValueError(f"Unsupported TaskStatusUpdate.status: {status!r}")

    # ---- SUCCESS --------------------------------------------------------

    async def _on_success(self, update: TaskStatusUpdate) -> None:
        task_id = update.task_id
        summary = update.summary or ""
        await self._mark_done_and_enqueue(task_id)
        task = self._graph.get(task_id)
        if task is not None and not task.summary:
            task.summary = summary
        if self._is_replanner(task_id):
            await self._finalize_replanned_origin_chain(task_id)
        await self._cascade_expanded_parent(task_id)

    async def _cascade_expanded_parent(self, child_id: str) -> None:
        """Walk up EXPANDED parents, synthesizing summaries and marking DONE."""
        current = child_id
        while True:
            parent_id = self._graph.find_promotable_parent(current)
            if parent_id is None:
                return
            await self._finalize_expanded_parent(parent_id)
            current = parent_id

    async def _finalize_expanded_parent(self, parent_id: str) -> None:
        parent = self._graph.get(parent_id)
        if parent is not None:
            parent.summary = _synthesize_parent_summary(parent_id, self._graph.tasks)
            if parent.plan is None:
                parent.plan = Plan()
        await self._mark_done_and_enqueue(parent_id)
        if self._is_replanner(parent_id):
            await self._finalize_replanned_origin_chain(parent_id)

    async def _sweep_promotable_parents(self) -> None:
        """Re-run promotion checks for every terminal child.

        Called after bulk graph changes (replan cancels) that can cause an
        EXPANDED parent to become promotable without a direct child-DONE event.
        """
        for child_id in self._graph.terminal_child_ids():
            await self._cascade_expanded_parent(child_id)

    async def _finalize_replanned_origin_chain(self, replanner_id: str) -> None:
        mutation = self._graph.finalize_replanned_origin(replanner_id)
        if mutation.is_empty():
            return
        origin_id = mutation.failure_reason_patches[0].task_id
        await self._store.persist(mutation)
        self._graph.apply(mutation)
        self._emit_status(origin_id)
        await self._cascade_expanded_parent(origin_id)

    def _is_replanner(self, task_id: str) -> bool:
        task = self._graph.get(task_id)
        return (
            task is not None
            and bool(task.fired_by_task_id)
            and _has_replanner_role(task.agent)
        )

    # ---- EXPANDED -------------------------------------------------------

    async def _on_expanded(self, update: TaskStatusUpdate) -> None:
        task_id = update.task_id
        task = self._graph.get(task_id)
        if task is None:
            await self._dispatch(_fail(task_id, "expand_target_missing"))
            return
        try:
            if update.replan is not None:
                await self._expand_replan(task, update.replan)
                return
            await self._expand_plan(task, update.plan)
        except InvalidPlan as exc:
            await self._dispatch(_fail(task_id, f"InvalidPlan: {exc}"))
        except BudgetExceeded as exc:
            await self._dispatch(_fail(task_id, f"BudgetExceeded: {exc}"))
        except GraphInvariantViolation as exc:
            await self._dispatch(_fail(task_id, f"GraphInvariantViolation: {exc}"))

    async def _expand_plan(self, task: Task, plan: Plan | None) -> None:
        outcome = self._expander.expand_submitted_plan(task, plan)
        if plan is None:
            # Non-planner expandable without children — finalize directly.
            await self._mark_done_and_enqueue(task.id)
            await self._cascade_expanded_parent(task.id)
            return

        mutation = self._graph.mark_expanded(task.id).merge(outcome.mutation)
        await self._store.persist(mutation)
        self._graph.apply(mutation)
        planner = self._graph.get(task.id)
        if planner is not None:
            planner.plan = plan
        self._emit_status(task.id)
        for new_task in outcome.new_tasks:
            self._emit(make_task_added(self._team_run_id, task_to_dict(new_task)))
        self._budget.emit_update()
        for new_task in outcome.new_tasks:
            if new_task.status is TaskStatus.READY:
                self._enqueue(new_task.id)

    async def _expand_replan(self, task: Task, replan: ReplanPlan) -> None:
        outcome = self._expander.apply_replan(
            replan_task=task,
            add_tasks=list(replan.add_tasks),
            cancel_ids=list(replan.cancel_ids),
        )
        # Cancel live runners for tasks that still showed RUNNING pre-mutation.
        if self._cancel_running_task is not None:
            for rid in outcome.cancelled_running_ids:
                self._cancel_running_task(rid)

        mutation = outcome.mutation
        if outcome.replanner_child_count > 0:
            mutation = self._graph.mark_expanded(task.id).merge(mutation)

        await self._store.persist(mutation)
        self._graph.apply(mutation)

        replanner = self._graph.get(task.id)
        if replanner is not None:
            replanner.plan = replan

        for cid in outcome.cancelled_ids:
            self._emit_status(cid)

        if outcome.replanner_child_count > 0:
            self._emit_status(task.id)
            for new_task in outcome.new_tasks:
                self._emit(make_task_added(self._team_run_id, task_to_dict(new_task)))
            self._budget.emit_update()
            await self._finalize_replanned_origin_chain(task.id)
            for new_task in outcome.new_tasks:
                if new_task.status is TaskStatus.READY:
                    self._enqueue(new_task.id)
        else:
            # Empty replan: fail the replanner rather than synthesizing success.
            await self._dispatch(_fail(task.id, "replan_produced_no_corrective_tasks"))
        # Replan cancels may have detached whole subtrees; sweep parents.
        await self._sweep_promotable_parents()

    # ---- REQUEST_REPLAN ------------------------------------------------

    async def _on_request_replan(self, update: TaskStatusUpdate) -> None:
        task_id = update.task_id
        try:
            self._budget.require_replan_capacity()
        except BudgetExceeded as exc:
            await self._dispatch(_fail(task_id, f"replan_budget_exhausted: {exc}"))
            return

        replanner_agent = _first_replanner_name()
        if replanner_agent is None:
            await self._dispatch(_fail(task_id, "no_replanner_registered"))
            return

        try:
            spawn = self._graph.plan_request_replan(
                task_id=task_id,
                reason=update.summary or "",
                replanner_agent=replanner_agent,
            )
        except GraphInvariantViolation as exc:
            await self._dispatch(_fail(task_id, f"GraphInvariantViolation: {exc}"))
            return

        await self._store.persist(spawn.mutation)
        self._graph.apply(spawn.mutation)

        if spawn.is_new:
            self._budget.bump_replan_counters()
            self._emit(
                make_task_added(
                    self._team_run_id, task_to_dict(spawn.replanner_task)
                )
            )
            for rewire in spawn.mutation.rewires:
                self._emit(
                    make_replace_dependency(
                        self._team_run_id,
                        old_dep_id=rewire.old_dep_id,
                        new_dep_ids=list(rewire.new_dep_ids),
                        task_ids=list(rewire.affected_task_ids),
                    )
                )
            self._budget.emit_update()
        self._emit_status(task_id)
        self._enqueue(spawn.replanner_task.id)

    # ---- CANCELLED -----------------------------------------------------

    async def _on_cancelled(self, update: TaskStatusUpdate) -> None:
        reason = update.summary or "cancelled"
        root_mutation = self._graph.cancel(update.task_id, reason)
        cascade_mutation, cascaded = self._graph.cancel_cascade(update.task_id)
        combined = root_mutation.merge(cascade_mutation)
        if combined.is_empty():
            return
        await self._store.persist(combined)
        self._graph.apply(combined)
        self._emit_status(update.task_id)
        for tid in cascaded:
            self._emit_status(tid)

    # ---- FAILED --------------------------------------------------------

    async def _on_failed(self, update: TaskStatusUpdate) -> None:
        reason = update.summary or "failed"
        mutation = self._graph.fail(update.task_id, reason)
        if not mutation.is_empty():
            await self._store.persist(mutation)
            self._graph.apply(mutation)
            self._emit_status(update.task_id)
        # Idempotent: if fail-fast is already in flight, observe the FAILED
        # row but skip re-triggering the run-level cancel wave.
        if self._cancel_event is not None and self._cancel_event.is_set():
            return
        await self._fail_fast(reason)

    # ---- shared mark-done helper ---------------------------------------

    async def _mark_done_and_enqueue(self, task_id: str) -> None:
        mutation = self._graph.promote_on_done(task_id)
        if mutation.is_empty():
            return
        await self._store.persist(mutation)
        self._graph.apply(mutation)
        promoted_ids: list[str] = []
        for change in mutation.status_changes:
            self._emit_status(change.task_id)
            if change.new_status is TaskStatus.READY:
                promoted_ids.append(change.task_id)
        self._enqueue_many(promoted_ids)

    # ---- queue / emit helpers ------------------------------------------

    def _enqueue(self, task_id: str) -> None:
        """Push ``task_id`` onto the ready queue iff its status is READY."""
        if self._queue is None:
            return
        task = self._graph.get(task_id)
        if task is None or task.status != TaskStatus.READY:
            return
        self._queue.enqueue(task_id)

    def _enqueue_many(self, task_ids: Iterable[str]) -> None:
        for tid in task_ids:
            self._enqueue(tid)

    def _emit_status(self, task_id: str) -> None:
        task = self._graph.get(task_id)
        if task is None:
            return
        self._emit(
            make_task_status(
                self._team_run_id,
                task.id,
                task.status.value,
                agent_run_id=task.agent_run_id,
                started_at=_iso(task.started_at),
                finished_at=_iso(task.finished_at),
                failure_reason=task.failure_reason,
            )
        )


# ---- module-level helpers ----------------------------------------------


def _fail(task_id: str, reason: str) -> TaskStatusUpdate:
    return TaskStatusUpdate(task_id=task_id, status=TaskStatus.FAILED, summary=reason)


def _synthesize_parent_summary(parent_id: str, graph: dict[str, Task]) -> str:
    """Build a parent's summary from its children.

    Priority:
    1. Terminal validator (reviewer role not depended on by any sibling,
       chosen by earliest ``created_at``) — use its submission summary.
    2. If no validator or the validator produced empty text, concatenate
       the summaries of terminal non-validator leaves ordered by
       ``created_at``, joined with ``\\n\\n---\\n\\n``.
    """
    children = [t for t in graph.values() if t.parent_id == parent_id]
    if not children:
        return ""
    from agents.registry import has_role

    sibling_deps = {d for c in children for d in (c.deps or [])}
    terminal_validators = sorted(
        (c for c in children if has_role(c.agent, "reviewer") and c.id not in sibling_deps),
        key=lambda t: t.created_at,
    )
    if terminal_validators:
        text = terminal_validators[0].summary.strip()
        if text:
            return text
    leaves = [
        c for c in children
        if c.id not in sibling_deps and not has_role(c.agent, "reviewer")
    ]
    parts = [
        text
        for text in (c.summary.strip() for c in sorted(leaves, key=lambda t: t.created_at))
        if text
    ]
    return "\n\n---\n\n".join(parts)


def _first_replanner_name() -> str | None:
    from agents.registry import find_by_role

    replanners = find_by_role("replanner")
    return replanners[0].name if replanners else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
