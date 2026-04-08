"""Dispatcher — DAG, ready queue, and atomic mutations for one TeamRun."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from team.errors import (
    ArtifactTooLarge,
    BudgetExceeded,
    CheckpointNotFound,
    InvalidPlan,
)
from team.models import (
    AgentResult,
    BudgetConfig,
    BudgetState,
    DependencyArtifact,
    TERMINAL_WI_STATUSES,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
    _utcnow,
)
from team.persistence.events import (
    TeamRunEvent,
    make_artifact_written,
    make_budget_update,
    make_checkpoint_taken,
    make_work_item_added,
    make_work_item_status,
    work_item_to_dict,
)
from team.persistence.run_store import NullTeamRunStore, TeamRunStore
from team.planning.validation import validate_plan_phase_b
from team.runtime.checkpoint import TeamRunCheckpoint

if TYPE_CHECKING:
    from team.artifacts.store import InMemoryArtifactStore


class Dispatcher:
    """Owns the WorkItem DAG for one TeamRun. Mutations are lock-protected."""

    def __init__(
        self,
        team_run_id: str,
        budgets: BudgetConfig,
        budget_state: BudgetState,
        artifact_store: "InMemoryArtifactStore",
        max_checkpoints: int = 10,
        event_store: TeamRunStore | None = None,
    ) -> None:
        self.team_run_id = team_run_id
        self.budgets = budgets
        self.budget_state = budget_state
        self.artifact_store = artifact_store
        self.graph: dict[str, WorkItem] = {}
        self._ready_queue: asyncio.Queue[str] = asyncio.Queue()
        self._ready_order: list[str] = []
        self.lock = asyncio.Lock()
        self._checkpoints: deque[TeamRunCheckpoint] = deque(maxlen=max_checkpoints)
        self._checkpoint_seq = 0
        self._events: TeamRunStore = event_store or NullTeamRunStore()

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
                work_items_used=self.budget_state.work_items_used,
                artifact_bytes_used=self.budget_state.artifact_bytes_used,
            )
        )

    def new_id(self) -> str:
        return str(uuid.uuid4())

    def _compute_readiness(self, wi: WorkItem) -> bool:
        """A WorkItem becomes READY iff PENDING and all deps are DONE."""
        if wi.status != WorkItemStatus.PENDING:
            return False
        for dep_id in wi.deps:
            dep = self.graph.get(dep_id)
            if dep is None or dep.status != WorkItemStatus.DONE:
                return False
        return True

    def _enqueue(self, wi: WorkItem) -> None:
        wi.status = WorkItemStatus.READY
        self._ready_queue.put_nowait(wi.id)
        self._ready_order.append(wi.id)
        self._emit(make_work_item_status(self.team_run_id, wi.id, "ready"))

    def _promote_to_ready(self, wi: WorkItem) -> None:
        """Single chokepoint for PENDING→READY: snapshots dep artifacts, then enqueues.

        Must be called from every path that transitions a WorkItem from
        PENDING to READY so that ``wi.dep_artifacts`` is captured exactly
        once from the frozen state of each dep at promotion time.
        """
        assert wi.status == WorkItemStatus.PENDING, (
            f"_promote_to_ready called on {wi.id} in status {wi.status.value}"
        )
        snapshot: list[DependencyArtifact] = []
        for dep_id in wi.deps:
            dep = self.graph.get(dep_id)
            if dep is None or dep.status != WorkItemStatus.DONE:
                raise RuntimeError(
                    f"_promote_to_ready called early: dep {dep_id} not DONE"
                )
            if dep.artifact_ref is None:
                continue
            snapshot.append(
                DependencyArtifact(
                    source_wi_id=dep.id,
                    artifact_ref=dep.artifact_ref,
                    display_name=dep.local_id or dep.agent_name or dep.id,
                )
            )
        wi.dep_artifacts = snapshot
        self._enqueue(wi)

    async def add_work_item(self, wi: WorkItem) -> None:
        async with self.lock:
            if self.budget_state.work_items_used >= self.budgets.max_work_items:
                raise BudgetExceeded(
                    f"max_work_items={self.budgets.max_work_items} reached"
                )
            if wi.id in self.graph:
                raise ValueError(f"WorkItem {wi.id} already exists")
            self.graph[wi.id] = wi
            self.budget_state.work_items_used += 1
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
                if wi is None or wi.status != WorkItemStatus.READY:
                    continue
                return wi_id

    async def mark_running(self, wi_id: str, agent_run_id: str) -> WorkItem:
        async with self.lock:
            wi = self.graph[wi_id]
            if wi.status != WorkItemStatus.READY:
                raise RuntimeError(
                    f"mark_running: {wi_id} is {wi.status.value}, not READY"
                )
            wi.status = WorkItemStatus.RUNNING
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

    async def complete(self, wi_id: str, result: AgentResult) -> list[WorkItem]:
        """Mark DONE and atomically insert any submitted Plan."""
        new_items: list[WorkItem] = []
        async with self.lock:
            wi = self.graph[wi_id]
            if wi.status != WorkItemStatus.RUNNING:
                raise RuntimeError(
                    f"complete: {wi_id} is {wi.status.value}, not RUNNING"
                )

            if wi.kind == WorkItemKind.EXPANDABLE and result.submitted_plan is None:
                wi.status = WorkItemStatus.FAILED
                wi.finished_at = _utcnow()
                wi.failure_reason = "InvalidPlan: expandable work item did not submit a plan"
                self._emit_failed(wi)
                self._cascade_cancel(wi_id)
                return []

            if result.submitted_plan is not None:
                try:
                    new_items = validate_plan_phase_b(
                        existing_graph=self.graph,
                        plan=result.submitted_plan,
                        team_run_id=self.team_run_id,
                        parent_wi=wi,
                        new_id_factory=self.new_id,
                        max_depth=self.budgets.max_depth,
                    )
                except InvalidPlan as e:
                    wi.status = WorkItemStatus.FAILED
                    wi.finished_at = _utcnow()
                    wi.failure_reason = f"InvalidPlan: {e}"
                    self._emit_failed(wi)
                    self._cascade_cancel(wi_id)
                    return []
                if (
                    self.budget_state.work_items_used + len(new_items)
                    > self.budgets.max_work_items
                ):
                    wi.status = WorkItemStatus.FAILED
                    wi.finished_at = _utcnow()
                    wi.failure_reason = "BudgetExceeded: max_work_items"
                    self._emit_failed(wi)
                    self._cascade_cancel(wi_id)
                    return []

            try:
                self.artifact_store.save(wi_id, result.artifact)
                wi.artifact_ref = wi_id
                self._emit(
                    make_artifact_written(
                        self.team_run_id,
                        wi_id=wi_id,
                        ref=wi_id,
                        size=self.artifact_store._sizes.get(wi_id, 0),
                        payload=result.artifact,
                    )
                )
            except ArtifactTooLarge as e:
                wi.status = WorkItemStatus.FAILED
                wi.finished_at = _utcnow()
                wi.failure_reason = f"ArtifactTooLarge: {e}"
                self._emit_failed(wi)
                self._cascade_cancel(wi_id)
                return []

            for nwi in new_items:
                self.graph[nwi.id] = nwi
                self.budget_state.work_items_used += 1
                self._emit(make_work_item_added(self.team_run_id, work_item_to_dict(nwi)))
            if new_items:
                self._emit_budget()

            wi.status = WorkItemStatus.DONE
            wi.finished_at = _utcnow()
            self._emit(
                make_work_item_status(
                    self.team_run_id,
                    wi_id,
                    "done",
                    finished_at=wi.finished_at.isoformat(),
                    artifact_ref=wi.artifact_ref,
                )
            )
            self._emit_budget()

            touched: list[WorkItem] = list(new_items)
            for other in self.graph.values():
                if wi_id in other.deps and other.status == WorkItemStatus.PENDING:
                    touched.append(other)
            for t in touched:
                if self._compute_readiness(t):
                    self._promote_to_ready(t)

        return new_items

    def _emit_failed(self, wi: WorkItem) -> None:
        self._emit(
            make_work_item_status(
                self.team_run_id,
                wi.id,
                "failed",
                finished_at=wi.finished_at.isoformat() if wi.finished_at else None,
                failure_reason=wi.failure_reason,
            )
        )

    async def fail(self, wi_id: str, reason: str) -> None:
        async with self.lock:
            wi = self.graph.get(wi_id)
            if wi is None or wi.status in TERMINAL_WI_STATUSES:
                return
            wi.status = WorkItemStatus.FAILED
            wi.finished_at = _utcnow()
            wi.failure_reason = reason
            self._emit_failed(wi)
            self._cascade_cancel(wi_id)

    def _cascade_cancel(self, wi_id: str) -> None:
        """Cancel everything transitively dependent on wi_id."""
        stack = [wi_id]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            for other in self.graph.values():
                if cur in other.deps and other.id not in seen:
                    seen.add(other.id)
                    if other.status not in TERMINAL_WI_STATUSES:
                        other.status = WorkItemStatus.CANCELLED
                        other.finished_at = _utcnow()
                        other.failure_reason = f"cascaded from {wi_id}"
                        self._emit(
                            make_work_item_status(
                                self.team_run_id,
                                other.id,
                                "cancelled",
                                finished_at=other.finished_at.isoformat(),
                                failure_reason=other.failure_reason,
                            )
                        )
                    stack.append(other.id)

    async def cancel_all_pending(self) -> None:
        async with self.lock:
            for wi in self.graph.values():
                if wi.status in (WorkItemStatus.PENDING, WorkItemStatus.READY):
                    wi.status = WorkItemStatus.CANCELLED
                    wi.finished_at = _utcnow()
                    wi.failure_reason = "team_run cancelled"
                    self._emit(
                        make_work_item_status(
                            self.team_run_id,
                            wi.id,
                            "cancelled",
                            finished_at=wi.finished_at.isoformat(),
                            failure_reason=wi.failure_reason,
                        )
                    )

    async def cancel_running(self, reason: str) -> None:
        """Mark any RUNNING items as CANCELLED. Used after a cooperative drain."""
        async with self.lock:
            for wi in self.graph.values():
                if wi.status == WorkItemStatus.RUNNING:
                    wi.status = WorkItemStatus.CANCELLED
                    wi.finished_at = _utcnow()
                    wi.failure_reason = reason
                    self._emit(
                        make_work_item_status(
                            self.team_run_id,
                            wi.id,
                            "cancelled",
                            finished_at=wi.finished_at.isoformat(),
                            failure_reason=reason,
                        )
                    )

    def all_terminal(self) -> bool:
        return all(wi.status in TERMINAL_WI_STATUSES for wi in self.graph.values())

    # ---- checkpoint / rollback -------------------------------------------

    async def checkpoint(
        self,
        label: str | None,
        project_context: Any,
    ) -> TeamRunCheckpoint:
        async with self.lock:
            self._checkpoint_seq += 1
            cp = TeamRunCheckpoint(
                id=str(uuid.uuid4()),
                team_run_id=self.team_run_id,
                sequence=self._checkpoint_seq,
                taken_at=_utcnow(),
                label=label,
                work_items=copy.deepcopy(self.graph),
                ready_queue_order=list(self._ready_order),
                artifacts=self.artifact_store.snapshot(),
                project_context=copy.deepcopy(project_context),
                budget_state=copy.deepcopy(self.budget_state),
            )
            self._checkpoints.append(cp)
            self._emit(
                make_checkpoint_taken(
                    self.team_run_id,
                    checkpoint_id=cp.id,
                    sequence=cp.sequence,
                    label=label,
                )
            )
            return cp

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
        async with self.lock:
            cp = self._get_checkpoint(checkpoint_id)
            if cp is None:
                raise CheckpointNotFound(checkpoint_id)

            self.graph = copy.deepcopy(cp.work_items)
            self.artifact_store.restore(cp.artifacts)
            self.budget_state.work_items_used = cp.budget_state.work_items_used
            self.budget_state.artifact_bytes_used = cp.budget_state.artifact_bytes_used
            project_context_setter(copy.deepcopy(cp.project_context))

            while not self._ready_queue.empty():
                self._ready_queue.get_nowait()
            self._ready_order = []
            for wi_id in cp.ready_queue_order:
                wi = self.graph.get(wi_id)
                if wi is not None and wi.status == WorkItemStatus.READY:
                    self._ready_queue.put_nowait(wi_id)
                    self._ready_order.append(wi_id)
            return cp
