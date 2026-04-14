"""TaskCenter — unified task lifecycle management.

Orchestrates NoteManager, ActivityTracker, CheckpointManager, and TaskStore.
TaskStore owns the in-memory task graph; TaskCenter owns orchestration logic.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from team.activity_tracker import ActivityTracker
from team.checkpoint_manager import CheckpointManager
from team.errors import BudgetExceeded, CheckpointNotFound, InvalidPlan
from team.models import (
    AgentResult,
    BudgetConfig,
    BudgetState,
    Note,
    ReplanRequest,
    RetryRequest,
    Task,
    TaskSpec,
    TaskStatus,
    _utcnow,
)
from team.note_manager import NoteManager
from team.persistence.events import (
    TeamRunEvent,
    make_budget_update,
    make_checkpoint_taken,
    make_task_added,
    make_task_status,
    task_to_dict,
)
from team.persistence.run_store import NullTeamRunStore, TeamRunStore
from team.persistence.task_record import TaskRecord
from team.persistence.task_store import TaskStore, record_to_task
from team.planning.validation import validate_plan
from team.runtime.checkpoint import TeamRunCheckpoint

logger = logging.getLogger(__name__)


class TaskCenter:
    """Unified task lifecycle management.

    Owns orchestration logic; TaskStore owns task graph and persistence.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        team_run_id: str,
        budgets: BudgetConfig,
        budget_state: BudgetState,
        goal: str = "",
        user_request: str = "",
        file_change_store: Any = None,
        max_checkpoints: int = 10,
        event_store: TeamRunStore | None = None,
        checkpoint_store: Any = None,
    ) -> None:
        self.goal = goal
        self.user_request = user_request
        self._team_run_id = team_run_id
        self._store = TaskStore(session_factory, team_run_id)
        self._file_change_store = file_change_store
        self.budgets = budgets
        self.budget_state = budget_state
        self._events: TeamRunStore = event_store or NullTeamRunStore()
        self._resume_snapshot: list[Task] | None = None
        self.lock = asyncio.Lock()

        self._notes = NoteManager(
            team_run_id=team_run_id,
            event_store_cb=self._emit,
            get_task_fn=lambda tid: self.get_task(tid),
            task_store=self._store,
        )

        def _on_note_posted(note: Note) -> None:
            self._activity.on_note_posted(note)

        self._activity = ActivityTracker(
            team_run_id=team_run_id,
            note_posted_cb=_on_note_posted,
        )

        self._checkpoints = CheckpointManager(
            team_run_id=team_run_id,
            max_checkpoints=max_checkpoints,
            checkpoint_store=checkpoint_store,
        )

    @property
    def graph(self) -> dict[str, Task]:
        return self._store.graph

    @property
    def _ready_order(self) -> list[str]:
        return self._store._ready_order

    async def get_task(self, task_id: str) -> Task | None:
        return self._store.get_task(task_id)

    def _emit(self, event: TeamRunEvent) -> None:
        try:
            self._events.append(event)
        except Exception:
            logger.exception("team event store append failed; continuing")

    def _emit_budget(self) -> None:
        self._emit(
            make_budget_update(
                self._team_run_id,
                tasks_used=self.budget_state.tasks_used,
                note_bytes_used=self.budget_state.note_bytes_used,
                replans_used=self.budget_state.replans_used,
            )
        )

    def new_id(self) -> str:
        return str(uuid.uuid4())

    def _charge_tasks(self, n: int = 1) -> None:
        self.budget_state.tasks_used += n
        self._emit_budget()

    @staticmethod
    def _iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    def _task_status_payload(self, task: Task) -> dict[str, Any]:
        return {
            "agent_run_id": task.agent_run_id,
            "started_at": self._iso(task.started_at),
            "finished_at": self._iso(task.finished_at),
            "failure_reason": task.failure_reason,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "blocker_id": task.blocker_id,
            "pause_checkpoint": task.pause_checkpoint,
            "pause_verdict": task.pause_verdict,
        }

    def _task_state_signature(self, task: Task | None) -> tuple[Any, ...] | None:
        if task is None:
            return None
        return (
            task.status.value,
            task.agent_run_id,
            self._iso(task.started_at),
            self._iso(task.finished_at),
            task.failure_reason,
            task.retry_count,
            task.max_retries,
            task.blocker_id,
            task.pause_checkpoint,
            task.pause_verdict,
        )

    def _task_state_snapshot(
        self, task_ids: set[str] | None = None
    ) -> dict[str, tuple[Any, ...] | None]:
        source_ids = task_ids or set(self.graph)
        return {
            task_id: self._task_state_signature(self.graph.get(task_id)) for task_id in source_ids
        }

    async def _refresh_graph_and_emit_transitions(
        self, before: dict[str, tuple[Any, ...] | None]
    ) -> None:
        await self._store.refresh_graph()
        for task_id, prior in before.items():
            task = self.graph.get(task_id)
            if task is None:
                continue
            current = self._task_state_signature(task)
            if current == prior:
                continue
            self._emit(
                make_task_status(
                    self._team_run_id, task.id, task.status.value, **self._task_status_payload(task)
                )
            )

    async def add_task(self, t: Task) -> None:
        if self.budget_state.tasks_used >= self.budgets.max_tasks:
            raise BudgetExceeded(f"max_tasks={self.budgets.max_tasks} reached")
        records = await self._store.insert_plan(
            [
                TaskSpec(
                    id=t.id,
                    task=t.task,
                    agent=t.agent_name,
                    deps=list(t.deps),
                    scope_paths=list(t.scope_paths),
                    cascade_policy=t.cascade_policy,
                )
            ],
            parent_id=t.parent_id,
            parent_depth=max(0, t.depth - 1) if t.parent_id else 0,
            parent_root_id=t.root_id or None,
        )
        self.budget_state.tasks_used += 1
        self._emit(make_task_added(self._team_run_id, task_to_dict(t)))
        self._emit_budget()

    async def _mark_failed_and_cascade(self, task_id: str, reason: str) -> None:
        before = self._task_state_snapshot()
        await self._store.mark_terminal(task_id, "failed", reason)
        await self._store.cascade_cancel_recursive(task_id)
        await self._refresh_graph_and_emit_transitions(before)

    async def complete_task(self, task_id: str, result: AgentResult) -> list[Task]:
        new_items: list[Task] = []
        rec = await self._store.get_record(task_id)
        if rec is None or rec.status != "running":
            raise RuntimeError(
                f"complete: {task_id} is {rec.status if rec else 'missing'}, not RUNNING"
            )

        from agents.registry import has_role as _has_role

        if _has_role(rec.agent_name, "planner") and result.submitted_plan is None:
            await self._mark_failed_and_cascade(
                task_id, "InvalidPlan: expandable task did not submit a plan"
            )
            return []

        if result.submitted_plan is not None:
            new_depth = (rec.depth or 0) + 1
            if new_depth > self.budgets.max_depth:
                await self._mark_failed_and_cascade(
                    task_id,
                    f"InvalidPlan: plan would exceed max_depth={self.budgets.max_depth} "
                    f"(current depth={rec.depth or 0}). Planners at the depth limit must "
                    f"emit developer tasks with broader scopes instead of nested team_planner tasks.",
                )
                return []
            adj = await self._store.get_adjacency()
            allow_empty = bool(rec.root_id) and task_id != (rec.root_id or task_id)
            issues = validate_plan(
                result.submitted_plan,
                max_plan_size=self.budgets.max_plan_size,
                allow_empty=allow_empty,
                known_external_deps=set(adj.keys()),
            )
            if issues:
                await self._mark_failed_and_cascade(
                    task_id, "InvalidPlan: " + "; ".join(i["msg"] for i in issues)
                )
                return []
            local_to_global: dict[str, str] = {
                spec.id: self.new_id() for spec in result.submitted_plan.tasks if spec.id
            }
            specs: list[TaskSpec] = []
            for spec in result.submitted_plan.tasks:
                nid = local_to_global.get(spec.id) or self.new_id()
                rdeps = [local_to_global[d] if d in local_to_global else d for d in spec.deps]
                specs.append(
                    TaskSpec(
                        id=nid,
                        task=spec.task,
                        agent=spec.agent,
                        deps=rdeps,
                        scope_paths=list(spec.scope_paths),
                        cascade_policy=spec.cascade_policy,
                    )
                )
                new_items.append(
                    Task(
                        id=nid,
                        team_run_id=self._team_run_id,
                        agent_name=spec.agent,
                        status=TaskStatus.READY if not rdeps else TaskStatus.PENDING,
                        task=spec.task,
                        deps=rdeps,
                        scope_paths=list(spec.scope_paths),
                        cascade_policy=spec.cascade_policy,
                        parent_id=task_id,
                        root_id=rec.root_id or task_id,
                        depth=new_depth,
                    )
                )
            if self.budget_state.tasks_used + len(new_items) > self.budgets.max_tasks:
                await self._mark_failed_and_cascade(task_id, "BudgetExceeded: max_tasks")
                return []
            inserted = await self._store.insert_plan(
                specs,
                parent_id=task_id,
                parent_depth=rec.depth or 0,
                parent_root_id=rec.root_id or task_id,
            )
            self.budget_state.tasks_used += len(new_items)
            actual_items = [record_to_task(item) for item in inserted]
            if actual_items:
                new_items = actual_items
            for item in new_items:
                self._emit(make_task_added(self._team_run_id, task_to_dict(item)))
            self._emit_budget()

        if result.submitted_plan is not None:
            await self._store.mark_expanded(task_id)
            self._emit(
                make_task_status(
                    self._team_run_id, task_id, "expanded", finished_at=_utcnow().isoformat()
                )
            )
        else:
            promoted_ready = await self._store.mark_done(task_id)
            self._emit(
                make_task_status(
                    self._team_run_id, task_id, "done", finished_at=_utcnow().isoformat()
                )
            )
            for dep_id in promoted_ready:
                dep_task = self.graph.get(dep_id)
                if dep_task is None:
                    continue
                self._emit(
                    make_task_status(
                        self._team_run_id,
                        dep_task.id,
                        dep_task.status.value,
                        **self._task_status_payload(dep_task),
                    )
                )
            for promoted_id in await self._store.maybe_promote_expanded_parent(task_id):
                promoted_task = self.graph.get(promoted_id)
                if promoted_task is None:
                    continue
                self._emit(
                    make_task_status(
                        self._team_run_id,
                        promoted_task.id,
                        promoted_task.status.value,
                        **self._task_status_payload(promoted_task),
                    )
                )

        if result.submitted_replan is not None:
            await self.apply_replan(
                replan_task_id=task_id,
                add_tasks=result.submitted_replan.add_tasks,
                cancel_ids=result.submitted_replan.cancel_ids,
                target_depth=rec.depth or 0,
                target_parent_id=rec.parent_id,
                target_root_id=rec.root_id or "",
            )
        await self._store.refresh_graph()
        return new_items

    async def fail(self, task_id: str, reason: str) -> None:
        before = self._task_state_snapshot()
        warnings = await self._store.fail_task(task_id, reason)
        for dep_id, msg in warnings:
            try:
                await self._notes.post(
                    Note(id=self.new_id(), task_id=dep_id, agent_name="system", content=msg)
                )
            except Exception:
                logger.debug("Failed to post warning note for %s", dep_id, exc_info=True)
        await self._refresh_graph_and_emit_transitions(before)

    async def retry_task(self, task_id: str, request: RetryRequest) -> None:
        rec = await self._store.get_record(task_id)
        if rec is None:
            raise RuntimeError(f"retry: {task_id} not found")
        before = self._task_state_snapshot({task_id})
        success = await self._store.retry_task(task_id, rec.max_retries)
        await self._refresh_graph_and_emit_transitions(before)
        if not success and task_id not in self.graph:
            self._emit(
                make_task_status(
                    self._team_run_id, task_id, "failed", failure_reason="retry_exhausted"
                )
            )

    async def request_replan(self, task_id: str, request: ReplanRequest) -> Task:
        if self.budget_state.replans_used >= self.budgets.max_replans_per_run:
            raise BudgetExceeded("max_replans_per_run reached")
        from agents.registry import find_by_role

        replanners = find_by_role("replanner")
        if not replanners:
            raise RuntimeError("no agent with role='replanner' is registered")
        before = self._task_state_snapshot({task_id})
        rec = await self._store.request_replan(
            task_id,
            reason=request.reason,
            suggestion=request.suggestion,
            replanner_agent=replanners[0].name,
        )
        self.budget_state.tasks_used += 1
        self.budget_state.replans_used += 1
        task = record_to_task(rec)
        self._emit(make_task_added(self._team_run_id, task_to_dict(task)))
        self._emit_budget()
        await self._refresh_graph_and_emit_transitions(before)
        return task

    async def apply_replan(
        self,
        replan_task_id: str,
        add_tasks: list[TaskSpec],
        cancel_ids: list[str],
        target_depth: int,
        target_parent_id: str | None,
        target_root_id: str,
    ) -> dict[str, int]:
        before = self._task_state_snapshot()
        from team.planning.validation import _has_cycle

        for cid in cancel_ids:
            rec = await self._store.get_record(cid)
            if rec is None:
                raise InvalidPlan(f"cancel target {cid} not found")
            if rec.parent_id != target_parent_id:
                raise InvalidPlan(
                    f"cancel target '{cid}' is a child of '{rec.parent_id}', not a sibling at your level. "
                    f"You can only cancel siblings (tasks with parent_id={target_parent_id!r}). "
                    f"To cancel '{cid}' and its entire subtree, cancel its parent '{rec.parent_id}' instead."
                )
            if rec.status not in ("pending", "ready", "expanded"):
                raise InvalidPlan(
                    f"cancel target {cid} is {rec.status}; can only cancel PENDING, READY, or EXPANDED"
                )
        local_to_new: dict[str, str] = {}
        for spec in add_tasks:
            if spec.id:
                if spec.id in local_to_new:
                    raise InvalidPlan(f"duplicate id '{spec.id}'")
                local_to_new[spec.id] = self.new_id()
        adj = await self._store.get_adjacency()
        clean_adj = {k: v for k, v in adj.items() if k not in set(cancel_ids)}
        specs: list[TaskSpec] = []
        for spec in add_tasks:
            nid = local_to_new.get(spec.id, self.new_id()) if spec.id else self.new_id()
            rdeps: list[str] = []
            for d in spec.deps:
                if d in local_to_new:
                    rdeps.append(local_to_new[d])
                elif d in adj:
                    rdeps.append(d)
                else:
                    raise InvalidPlan(f"replan dep '{d}' is not a local alias or existing task id")
            clean_adj[nid] = rdeps
            specs.append(
                TaskSpec(
                    id=nid,
                    task=spec.task,
                    agent=spec.agent,
                    deps=rdeps,
                    scope_paths=list(spec.scope_paths),
                    cascade_policy=spec.cascade_policy,
                )
            )
        if _has_cycle(clean_adj):
            raise InvalidPlan("replan would create a cycle")
        if self.budget_state.tasks_used + len(specs) > self.budgets.max_tasks:
            raise BudgetExceeded("max_tasks would be exceeded by replan")
        await self._store.cancel_by_ids(cancel_ids, f"cancelled_by_replan_{replan_task_id}")
        for cid in cancel_ids:
            await self._store.cascade_cancel_recursive(cid)
        if specs:
            inserted = await self._store.insert_plan(
                specs,
                parent_id=target_parent_id,
                parent_depth=max(0, target_depth - 1),
                parent_root_id=target_root_id or None,
            )
            self._charge_tasks(len(specs))
            for item in inserted:
                self._emit(make_task_added(self._team_run_id, task_to_dict(record_to_task(item))))
        await self._refresh_graph_and_emit_transitions(before)
        return {"added": len(specs), "cancelled": len(cancel_ids)}

    async def cancel_all_pending(self) -> int:
        before = self._task_state_snapshot()
        count = await self._store.cancel_all_pending()
        if count:
            await self._refresh_graph_and_emit_transitions(before)
        return count

    async def cancel_all_running(self, reason: str) -> int:
        before = self._task_state_snapshot()
        count = await self._store.cancel_all_running(reason)
        if count:
            await self._refresh_graph_and_emit_transitions(before)
        return count

    async def cancel_paused_tasks(self, blocker_id: str) -> int:
        affected_ids = {
            tid
            for tid, t in self.graph.items()
            if t.blocker_id == blocker_id and t.status == TaskStatus.PAUSED
        }
        before = self._task_state_snapshot(affected_ids)
        count = await self._store.cancel_paused_tasks(blocker_id)
        if count and affected_ids:
            await self._refresh_graph_and_emit_transitions(before)
        return count

    async def resume_paused_tasks(self, blocker_id: str) -> int:
        affected_ids = {
            tid
            for tid, t in self.graph.items()
            if t.blocker_id == blocker_id and t.status == TaskStatus.PAUSED
        }
        before = self._task_state_snapshot(affected_ids)
        count = await self._store.resume_paused_tasks(blocker_id)
        if count and affected_ids:
            await self._refresh_graph_and_emit_transitions(before)
        return count

    async def pause_running_task(
        self, task_id: str, blocker_id: str, checkpoint: str, verdict: str
    ) -> bool:
        paused = await self._store.pause_running_task(task_id, blocker_id, checkpoint, verdict)
        if not paused:
            return False
        task = self.graph.get(task_id)
        if task is not None:
            self._emit(
                make_task_status(
                    self._team_run_id, task.id, task.status.value, **self._task_status_payload(task)
                )
            )
        return True

    async def mark_running(self, task_id: str, agent_run_id: str) -> Task:
        rec = await self._store.mark_running_sql(task_id, agent_run_id)
        if rec is None:
            raise RuntimeError(f"mark_running: {task_id} not found")
        task = record_to_task(rec)
        self._emit(
            make_task_status(
                self._team_run_id,
                task_id,
                "running",
                agent_run_id=agent_run_id,
                started_at=task.started_at.isoformat() if task.started_at else None,
            )
        )
        return task

    async def checkpoint(self, label: str | None, project_context: Any) -> TeamRunCheckpoint:
        await self._store.refresh_graph()
        return await self._checkpoints.checkpoint(
            label=label,
            project_context=project_context,
            tasks=self.graph,
            ready_queue_order=self._ready_order,
            budget_state=self.budget_state,
            emit_checkpoint_cb=lambda run_id, cp_id, seq, lbl: self._emit(
                make_checkpoint_taken(run_id, checkpoint_id=cp_id, sequence=seq, label=lbl)
            ),
        )

    def list_checkpoints(self) -> list[TeamRunCheckpoint]:
        return self._checkpoints.list_checkpoints()

    async def rollback_to(
        self, checkpoint_id: str, project_context_setter: Any
    ) -> TeamRunCheckpoint:
        cp = await self._checkpoints.rollback_to(
            checkpoint_id=checkpoint_id,
            project_context_setter=project_context_setter,
            replace_run_tasks_fn=self._store.replace_run_tasks,
        )
        if cp is None:
            raise CheckpointNotFound(checkpoint_id)
        await self._store.refresh_graph()
        self.budget_state = cp.budget_state
        return cp

    async def prepare_for_resume(self) -> None:
        await self._checkpoints.prepare_for_resume(
            resume_snapshot=self._resume_snapshot,
            recover_running_fn=self._store.recover_running,
            replace_run_tasks_fn=self._store.replace_run_tasks,
        )
        self._resume_snapshot = None
        await self._store.refresh_graph()

    async def context_for(self, task: Task, *, max_context_bytes: int = 200_000) -> str:
        return await self._notes.context_for(
            task, max_context_bytes=max_context_bytes, file_change_store=self._file_change_store
        )

    async def post(self, note: Note) -> None:
        await self._notes.post(note)

    async def read(
        self,
        *,
        authors: list[str] | None = None,
        scope_paths: list[str] | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[Note]:
        return await self._notes.read(
            authors=authors, scope_paths=scope_paths, since=since, limit=limit
        )

    async def read_notes(
        self,
        *,
        task_id: str,
        scope: str = "full",
        keyword: str | None = None,
        scope_paths: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Note]:
        return await self._notes.read_notes(
            task_id=task_id, scope=scope, keyword=keyword, scope_paths=scope_paths, limit=limit
        )

    async def read_sibling_notes(
        self, parent_id: str, *, keyword: str | None = None, scope_paths: list[str] | None = None
    ) -> str:
        return await self._notes.read_sibling_notes(
            parent_id=parent_id, keyword=keyword, scope_paths=scope_paths
        )

    def snapshot(self) -> list[Note]:
        return self._notes.snapshot()

    def restore(self, notes: list[Note]) -> None:
        self._notes.restore(notes)

    def on_edit(self, task_id: str, file_path: str) -> None:
        self._activity.on_edit(task_id, file_path)

    def on_posthook(self, task_id: str) -> None:
        self._activity.on_posthook(task_id)

    def tick(self, task_id: str) -> None:
        self._activity.tick(task_id)

    def should_checkpoint(self, task_id: str) -> str | None:
        return self._activity.should_checkpoint(task_id)

    def _get_counters(self, task_id: str) -> dict[str, Any]:
        return self._activity._get_counters(task_id)

    async def check(
        self,
        task_id: str,
        *,
        snapshot: list[dict] | None = None,
        api_client: Any = None,
        model: str | None = None,
    ) -> bool:
        task = self.graph.get(task_id)
        agent_name = task.agent_name if task else "unknown"
        scope_paths = list(task.scope_paths) if task and task.scope_paths else []
        agent_run_id = task.agent_run_id if task else task_id
        return await self._activity.check(
            task_id=task_id,
            graph=self.graph,
            scope_paths=scope_paths,
            agent_name=agent_name,
            agent_run_id=agent_run_id,
            snapshot=snapshot,
            api_client=api_client,
            model=model,
            post_note_cb=self._notes.post,
        )

    async def recover_running(self) -> list[TaskRecord]:
        return await self._store.recover_running()

    async def cascade_cancel_recursive(self, root_task_id: str) -> list[str]:
        return await self._store.cascade_cancel_recursive(root_task_id)

    async def replace_run_tasks(self, tasks: list[Task]) -> None:
        await self._store.replace_run_tasks(tasks)

    async def compute_final_statuses(self) -> set[str]:
        return set((await self._store.get_statuses()).values())

    async def known_task_ids(self) -> set[str]:
        return await self._store.get_task_ids()

    async def done_sibling_ids(
        self, *, task_id: str, parent_id: str | None, since: float | None = None
    ) -> list[str]:
        return await self._store.get_done_sibling_ids(
            task_id=task_id, parent_id=parent_id, since=since
        )

    async def all_terminal(self) -> bool:
        return await self._store.all_terminal()

    async def sibling_stats(self, parent_id: str | None) -> dict[str, int]:
        return await self._store.sibling_stats(parent_id)

    async def insert_plan(
        self,
        specs: list[TaskSpec],
        parent_id: str | None = None,
        parent_depth: int = 0,
        parent_root_id: str | None = None,
    ) -> list[TaskRecord]:
        return await self._store.insert_plan(specs, parent_id, parent_depth, parent_root_id)

    async def get_all_tasks(self) -> list[TaskRecord]:
        return await self._store.get_all_tasks()

    async def get_adjacency(self) -> dict[str, list[str]]:
        return await self._store.get_adjacency()

    async def get_statuses(self) -> dict[str, str]:
        return await self._store.get_statuses()

    async def get_task_ids(self) -> set[str]:
        return await self._store.get_task_ids()

    async def get_siblings_and_descendants(self, initiating_task_id: str) -> list[TaskRecord]:
        return await self._store.get_siblings_and_descendants(initiating_task_id)
