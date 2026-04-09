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
                replans_used=self.budget_state.replans_used,
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

    # ---- retry / replan --------------------------------------------------

    async def retry_work_item(self, wi_id: str, request: "RetryRequest") -> None:
        """Reset a RUNNING work item back to READY for re-execution."""
        from team.models import RetryRequest as _RR  # noqa: F811

        async with self.lock:
            wi = self.graph[wi_id]
            if wi.status != WorkItemStatus.RUNNING:
                raise RuntimeError(f"retry: {wi_id} is {wi.status.value}, not RUNNING")
            if wi.retry_count >= wi.max_retries:
                wi.status = WorkItemStatus.FAILED
                wi.finished_at = _utcnow()
                wi.failure_reason = f"retry_exhausted: {request.reason}"
                self._emit_failed(wi)
                self._cascade_cancel(wi_id)
                return
            wi.retry_count += 1
            wi.agent_run_id = None
            wi.started_at = None
            wi.status = WorkItemStatus.PENDING
            retries = wi.payload.setdefault("_retry_history", [])
            retries.append({"attempt": wi.retry_count, "reason": request.reason})
            self._emit(make_work_item_status(self.team_run_id, wi_id, "pending"))
            self._promote_to_ready(wi)

    async def request_replan(self, wi_id: str, request: "ReplanRequest") -> WorkItem:
        """Fail the work item and spawn an ATOMIC replanner at the same depth level."""
        async with self.lock:
            wi = self.graph[wi_id]
            if wi.status != WorkItemStatus.RUNNING:
                raise RuntimeError(f"replan: {wi_id} is {wi.status.value}, not RUNNING")

            if self.budget_state.replans_used >= self.budgets.max_replans_per_run:
                wi.status = WorkItemStatus.FAILED
                wi.finished_at = _utcnow()
                wi.failure_reason = f"replan_budget_exhausted: {request.reason}"
                self._emit_failed(wi)
                self._cascade_cancel(wi_id)
                raise BudgetExceeded("max_replans_per_run reached")

            # 1. Fail the current work item
            wi.status = WorkItemStatus.FAILED
            wi.finished_at = _utcnow()
            wi.failure_reason = f"replan_requested: {request.reason}"
            self._emit_failed(wi)

            # 2. Cancel PENDING and READY siblings (not RUNNING)
            for other in list(self.graph.values()):
                if (
                    other.parent_id == wi.parent_id
                    and other.id != wi_id
                    and other.status in (WorkItemStatus.PENDING, WorkItemStatus.READY)
                ):
                    other.status = WorkItemStatus.CANCELLED
                    other.finished_at = _utcnow()
                    other.failure_reason = f"cancelled_by_replan_from_{wi_id}"
                    self._emit(
                        make_work_item_status(
                            self.team_run_id, other.id, "cancelled",
                            finished_at=other.finished_at.isoformat(),
                            failure_reason=other.failure_reason,
                        )
                    )
                    self._cascade_cancel(other.id)

            # 3. Cancel downstream dependents of failed item
            self._cascade_cancel(wi_id)

            # 4. Collect DONE siblings as deps for replanner
            done_sibling_ids = [
                other.id
                for other in self.graph.values()
                if other.parent_id == wi.parent_id
                and other.id != wi_id
                and other.status == WorkItemStatus.DONE
            ]

            # 5. Create ATOMIC replanner
            from team.builtins import TEAM_REPLANNER

            replanner_id = self.new_id()
            replanner = WorkItem(
                id=replanner_id,
                team_run_id=self.team_run_id,
                agent_name=TEAM_REPLANNER,
                status=WorkItemStatus.PENDING,
                kind=WorkItemKind.ATOMIC,
                deps=done_sibling_ids,
                parent_id=wi.parent_id,
                root_id=wi.root_id,
                depth=wi.depth,
                local_id=f"replan-from-{wi.local_id or wi_id}",
                payload={
                    "replan": True,
                    "failed_work_item_id": wi_id,
                    "failed_agent": wi.agent_name,
                    "failure_reason": request.reason,
                    "failure_context": request.context,
                    "suggestion": request.suggestion,
                    "original_payload": wi.payload,
                },
                briefings=list(wi.briefings),
                replan_source_id=wi_id,
            )
            self.graph[replanner_id] = replanner
            self.budget_state.work_items_used += 1
            self.budget_state.replans_used += 1
            self._emit(make_work_item_added(self.team_run_id, work_item_to_dict(replanner)))
            self._emit_budget()

            if self._compute_readiness(replanner):
                self._promote_to_ready(replanner)

            return replanner

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

    async def prepare_for_resume(self) -> None:
        """Normalize live state after process loss and rebuild the ready queue."""
        async with self.lock:
            while not self._ready_queue.empty():
                self._ready_queue.get_nowait()
            self._ready_order = []

            for wi in self.graph.values():
                if wi.status == WorkItemStatus.RUNNING:
                    wi.status = WorkItemStatus.READY
                    wi.agent_run_id = None
                    wi.started_at = None
                    self._ready_queue.put_nowait(wi.id)
                    self._ready_order.append(wi.id)
                    self._emit(make_work_item_status(self.team_run_id, wi.id, "ready"))
                    continue

                if wi.status == WorkItemStatus.READY:
                    self._ready_queue.put_nowait(wi.id)
                    self._ready_order.append(wi.id)
                    continue

                if self._compute_readiness(wi):
                    self._promote_to_ready(wi)

    # ---- replan: lateral DAG mutation ------------------------------------

    async def apply_replan(
        self,
        replan_wi_id: str,
        add_specs: list[dict],
        cancel_ids: list[str],
        target_depth: int,
        target_parent_id: str | None,
        target_root_id: str,
    ) -> dict[str, int]:
        """Atomically cancel stale items and insert corrective items at the target level.

        Unlike ``complete()`` + ``validate_plan_phase_b`` (which always creates
        children at ``depth + 1``), this method inserts items at a specified
        depth and parent — enabling true sibling-level replacement.

        ``cancel_ids`` must share the same ``parent_id`` as the target to
        enforce scoping to the current plan level.
        """
        from team.models import Briefing

        async with self.lock:
            # --- Validate cancellations (scoped to same parent) ---
            for cid in cancel_ids:
                wi = self.graph.get(cid)
                if wi is None:
                    raise InvalidPlan(f"cancel target {cid} not found")
                if wi.parent_id != target_parent_id:
                    raise InvalidPlan(
                        f"cancel target {cid} has parent {wi.parent_id!r}, "
                        f"but replan is scoped to parent {target_parent_id!r}"
                    )
                if wi.status not in (WorkItemStatus.PENDING, WorkItemStatus.READY):
                    raise InvalidPlan(
                        f"cancel target {cid} is {wi.status.value}; "
                        f"can only cancel PENDING or READY items"
                    )

            # --- Resolve local_ids ---
            local_to_new: dict[str, str] = {}
            for spec in add_specs:
                lid = spec.get("local_id")
                if lid:
                    if lid in local_to_new:
                        raise InvalidPlan(f"duplicate local_id '{lid}'")
                    local_to_new[lid] = self.new_id()

            # --- Build new WorkItems ---
            new_items: list[WorkItem] = []
            for spec in add_specs:
                lid = spec.get("local_id")
                new_id = local_to_new.get(lid, self.new_id()) if lid else self.new_id()
                resolved_deps: list[str] = []
                for dep in spec.get("deps") or []:
                    if dep in local_to_new:
                        resolved_deps.append(local_to_new[dep])
                    elif dep in self.graph:
                        resolved_deps.append(dep)
                    else:
                        raise InvalidPlan(f"dep '{dep}' not found")

                briefings = [Briefing(**b) for b in (spec.get("briefings") or [])]
                new_items.append(
                    WorkItem(
                        id=new_id,
                        team_run_id=self.team_run_id,
                        agent_name=spec["agent_name"],
                        status=WorkItemStatus.PENDING,
                        kind=WorkItemKind(spec.get("kind", "atomic")),
                        deps=resolved_deps,
                        parent_id=target_parent_id,
                        root_id=target_root_id,
                        depth=target_depth,
                        local_id=lid,
                        payload=dict(spec.get("payload") or {}),
                        timeout_seconds=spec.get("timeout_seconds"),
                        briefings=briefings,
                    )
                )

            # --- Budget check ---
            if self.budget_state.work_items_used + len(new_items) > self.budgets.max_work_items:
                raise BudgetExceeded("max_work_items would be exceeded by replan")

            # --- Cycle detection on merged graph ---
            cancelled_set = set(cancel_ids)
            combined_adj: dict[str, list[str]] = {}
            for wi_id_key, wi in self.graph.items():
                if wi_id_key not in cancelled_set:
                    combined_adj[wi_id_key] = list(wi.deps)
            for nwi in new_items:
                combined_adj[nwi.id] = list(nwi.deps)

            # Topological sort check (DFS-based cycle detection)
            visited: set[str] = set()
            on_stack: set[str] = set()

            def _has_cycle_from(node: str) -> bool:
                if node in on_stack:
                    return True
                if node in visited:
                    return False
                visited.add(node)
                on_stack.add(node)
                for nb in combined_adj.get(node, []):
                    if _has_cycle_from(nb):
                        return True
                on_stack.discard(node)
                return False

            for start in combined_adj:
                if _has_cycle_from(start):
                    raise InvalidPlan("replan would create a cycle in the combined graph")

            # --- Apply atomically ---
            # 1. Cancel stale items + cascade dependents
            for cid in cancel_ids:
                wi = self.graph[cid]
                wi.status = WorkItemStatus.CANCELLED
                wi.finished_at = _utcnow()
                wi.failure_reason = f"cancelled_by_replan_{replan_wi_id}"
                self._emit(
                    make_work_item_status(
                        self.team_run_id, cid, "cancelled",
                        finished_at=wi.finished_at.isoformat(),
                        failure_reason=wi.failure_reason,
                    )
                )
                self._cascade_cancel(cid)

            # 2. Insert new items
            for nwi in new_items:
                self.graph[nwi.id] = nwi
                self.budget_state.work_items_used += 1
                self._emit(make_work_item_added(self.team_run_id, work_item_to_dict(nwi)))

            if new_items:
                self._emit_budget()

            # 3. Promote newly ready items
            for nwi in new_items:
                if self._compute_readiness(nwi):
                    self._promote_to_ready(nwi)

            return {"added": len(new_items), "cancelled": len(cancel_ids)}
