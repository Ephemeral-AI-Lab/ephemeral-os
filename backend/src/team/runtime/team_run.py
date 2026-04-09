"""TeamRun lifecycle container."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from team.artifacts.store import InMemoryArtifactStore
from team.atlas.identity import project_key_for
from team.context.project import ProjectContext
from team.persistence.events import (
    make_team_run_created,
    make_team_run_status,
)
from team.persistence.run_store import NullTeamRunStore, TeamRunStore, build_default_store
from team.models import (
    Briefing,
    BudgetConfig,
    BudgetState,
    DependencyArtifact,
    TeamDefinition,
    TeamRunStatus,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)
from team.runtime.dispatcher import Dispatcher
from team.runtime.executor import Executor
from team.runtime.registry import register as _register_team_run
from team.runtime.registry import unregister as _unregister_team_run

if TYPE_CHECKING:
    from team.atlas.scheduler import AtlasMaintenanceScheduler


@dataclass(frozen=True)
class TeamRuntimeServices:
    """Concrete collaborators used by a TeamRun."""

    project_context: ProjectContext
    artifact_store: InMemoryArtifactStore
    dispatcher: Dispatcher
    event_store: TeamRunStore


def build_team_runtime_services(
    *,
    team_run_id: str,
    budgets: BudgetConfig,
    budget_state: BudgetState,
    user_request: str,
    goal: str | None = None,
    repo_root: str | None = None,
    event_store: TeamRunStore | None = None,
) -> TeamRuntimeServices:
    """Build the default in-memory runtime collaborators for a TeamRun.

    ``event_store`` is an optional durable event sink; when omitted the
    factory consults ``EPHEMERALOS_TEAM_RUN_DIR`` (jsonl) and falls back
    to ``NullTeamRunStore``. Pass an explicit store in tests.
    """
    project_context = ProjectContext(
        goal=goal or user_request,
        user_request=user_request,
        repo_root=repo_root or "",
        project_key=project_key_for(repo_root),
    )
    artifact_store = InMemoryArtifactStore(budgets, budget_state)
    store = event_store if event_store is not None else build_default_store()
    dispatcher = Dispatcher(
        team_run_id=team_run_id,
        budgets=budgets,
        budget_state=budget_state,
        artifact_store=artifact_store,
        event_store=store,
    )
    return TeamRuntimeServices(
        project_context=project_context,
        artifact_store=artifact_store,
        dispatcher=dispatcher,
        event_store=store,
    )


class TeamRun:
    def __init__(
        self,
        *,
        session_id: str,
        user_request: str,
        budgets: BudgetConfig | None = None,
        goal: str | None = None,
        sandbox_id: str | None = None,
        repo_root: str | None = None,
        team_run_id: str | None = None,
        event_store: TeamRunStore | None = None,
        services: TeamRuntimeServices | None = None,
    ) -> None:
        self.id = team_run_id or str(uuid.uuid4())
        self.session_id = session_id
        self.user_request = user_request
        self.sandbox_id = sandbox_id
        self.budgets = budgets or BudgetConfig()
        self.budget_state = BudgetState()
        self.status = TeamRunStatus.PENDING
        runtime_services = services or build_team_runtime_services(
            team_run_id=self.id,
            budgets=self.budgets,
            budget_state=self.budget_state,
            user_request=user_request,
            goal=goal,
            repo_root=repo_root,
            event_store=event_store,
        )
        self.budgets = runtime_services.dispatcher.budgets
        self.budget_state = runtime_services.dispatcher.budget_state
        self.project_context = runtime_services.project_context
        self.artifacts = runtime_services.artifact_store
        self.dispatcher = runtime_services.dispatcher
        self.event_store: TeamRunStore = getattr(
            runtime_services, "event_store", NullTeamRunStore()
        )
        self.cancel_event = asyncio.Event()
        self.root_work_item_id: str | None = None
        self._executor_tasks: list[asyncio.Task[None]] = []
        self._executor_factory: Callable[["TeamRun"], Executor] | None = None
        self._num_executors: int = 1
        self.atlas_scheduler: AtlasMaintenanceScheduler | None = None

    # ---- lifecycle -------------------------------------------------------

    async def start(
        self,
        agent_name: str,
        payload: dict[str, Any],
        *,
        executor_factory: Callable[["TeamRun"], Executor],
        atlas_scheduler_factory: Callable[
            ["TeamRun"], "AtlasMaintenanceScheduler"
        ] | None = None,
        num_executors: int = 1,
        root_kind: WorkItemKind = WorkItemKind.ATOMIC,
    ) -> None:
        root = WorkItem(
            id=str(uuid.uuid4()),
            team_run_id=self.id,
            agent_name=agent_name,
            status=WorkItemStatus.PENDING,
            payload=dict(payload),
            depth=0,
            kind=root_kind,
        )
        root.root_id = root.id
        self.root_work_item_id = root.id
        # Durable record of the run *before* any work items exist so a
        # crash during dispatch still leaves a recoverable header.
        self.event_store.append(
            make_team_run_created(
                self.id,
                session_id=self.session_id,
                user_request=self.user_request,
                goal=None,
                repo_root=self.project_context.repo_root,
                sandbox_id=self.sandbox_id,
                budgets=asdict(self.budgets),
            )
        )
        await self.dispatcher.add_work_item(root)
        self.status = TeamRunStatus.RUNNING
        self.event_store.append(make_team_run_status(self.id, self.status.value))
        _register_team_run(self)
        if atlas_scheduler_factory is not None:
            self.atlas_scheduler = atlas_scheduler_factory(self)
            await self.atlas_scheduler.start()

        self._executor_factory = executor_factory
        self._num_executors = num_executors
        self._spawn_executors()

    async def start_with_team_definition(
        self,
        team_def: TeamDefinition,
        payload: dict[str, Any],
        *,
        executor_factory: Callable[["TeamRun"], Executor],
        atlas_scheduler_factory: Callable[
            ["TeamRun"], "AtlasMaintenanceScheduler"
        ] | None = None,
        num_executors: int = 1,
    ) -> None:
        """Start a team run using a ``TeamDefinition`` to pick the planner.

        Validates that ``team_def.planner_agent`` resolves in ``agents.registry``
        before dispatching the root WorkItem. Broken references fail fast
        with a descriptive error; the TeamRun stays in ``PENDING`` status
        and no workers are spawned.
        """
        # Lazy import — avoids a module-level dependency cycle between
        # ``team.runtime.team_run`` and ``agents.registry``.
        from agents.registry import get_definition

        if get_definition(team_def.planner_agent) is None:
            raise ValueError(
                f"team_definition '{team_def.name}' references planner agent "
                f"'{team_def.planner_agent}' which does not exist"
            )
        await self.start(
            agent_name=team_def.planner_agent,
            payload=payload,
            executor_factory=executor_factory,
            atlas_scheduler_factory=atlas_scheduler_factory,
            num_executors=num_executors,
            root_kind=WorkItemKind.EXPANDABLE,
        )

    def _spawn_executors(self) -> None:
        assert self._executor_factory is not None, "executor_factory not set"
        for _ in range(self._num_executors):
            executor = self._executor_factory(self)
            self._executor_tasks.append(asyncio.create_task(executor.run_forever()))

    async def wait(self) -> TeamRunStatus:
        try:
            while not self.dispatcher.all_terminal():
                await asyncio.sleep(0.05)
            await self._join_executors()
            self._compute_final_status()
            return self.status
        finally:
            if self.atlas_scheduler is not None:
                await self.atlas_scheduler.stop()
                self.atlas_scheduler = None
            _unregister_team_run(self.id)

    async def _join_executors(self) -> None:
        """Cooperative shutdown after the DAG has reached a terminal state.

        Unlike ``_drain_executors``, this does NOT cancel running items —
        the graph is already terminal so no item should be RUNNING.
        """
        self.cancel_event.set()
        for t in self._executor_tasks:
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        self._executor_tasks = []
        self.cancel_event.clear()

    async def _drain_executors(self) -> None:
        """Forceful drain used by rollback/cancel — kills any RUNNING item."""
        self.cancel_event.set()
        for t in self._executor_tasks:
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        self._executor_tasks = []
        await self.dispatcher.cancel_running("drained by rollback/cancel")
        self.cancel_event.clear()

    def _compute_final_status(self) -> None:
        statuses = {wi.status for wi in self.dispatcher.graph.values()}
        if WorkItemStatus.FAILED in statuses:
            self.status = TeamRunStatus.FAILED
        elif WorkItemStatus.CANCELLED in statuses:
            self.status = TeamRunStatus.CANCELLED
        else:
            self.status = TeamRunStatus.SUCCEEDED
        self.event_store.append(make_team_run_status(self.id, self.status.value))

    async def cancel(self) -> None:
        self.cancel_event.set()
        await self.dispatcher.cancel_all_pending()

    def note_atlas_lookup(
        self,
        entries: list[dict[str, Any]],
        *,
        source: str = "atlas_lookup",
    ) -> None:
        if self.atlas_scheduler is None:
            return
        try:
            self.atlas_scheduler.note_lookup(entries, source=source)
        except Exception:
            import logging

            logging.getLogger(__name__).debug(
                "atlas scheduler note_lookup failed",
                exc_info=True,
            )

    def note_atlas_edit(self, file_path: str, *, reason: str = "edit") -> None:
        if self.atlas_scheduler is None:
            return
        try:
            self.atlas_scheduler.mark_dirty_path(file_path, reason=reason)
        except Exception:
            import logging

            logging.getLogger(__name__).debug(
                "atlas scheduler mark_dirty_path failed",
                exc_info=True,
            )

    # ---- checkpoint API --------------------------------------------------

    async def checkpoint(self, label: str | None = None) -> str:
        cp = await self.dispatcher.checkpoint(
            label=label,
            project_context=self.project_context,
        )
        return cp.id

    async def rollback_to(self, checkpoint_id: str) -> None:
        # Phase 1 — cooperative drain.
        self.cancel_event.set()
        await self._drain_executors()
        # Phase 2 — atomic restore.
        await self.dispatcher.rollback_to(
            checkpoint_id,
            project_context_setter=lambda pc: setattr(self, "project_context", pc),
        )
        self.cancel_event.clear()
        # Phase 3 — respawn workers so the restored DAG actually drains.
        if self._executor_factory is not None:
            self._spawn_executors()

    async def resume(
        self,
        *,
        executor_factory: Callable[["TeamRun"], Executor],
        atlas_scheduler_factory: Callable[
            ["TeamRun"], "AtlasMaintenanceScheduler"
        ] | None = None,
        num_executors: int = 1,
    ) -> None:
        """Resume a rehydrated TeamRun in the current process."""
        if self.dispatcher.all_terminal():
            return

        await self.dispatcher.prepare_for_resume()
        self.cancel_event.clear()
        self._executor_factory = executor_factory
        self._num_executors = num_executors
        self.status = TeamRunStatus.RUNNING
        self.event_store.append(make_team_run_status(self.id, self.status.value))
        _register_team_run(self)
        if atlas_scheduler_factory is not None:
            self.atlas_scheduler = atlas_scheduler_factory(self)
            await self.atlas_scheduler.start()
        self._spawn_executors()

    # ---- crash recovery --------------------------------------------------

    @classmethod
    def resume_from(
        cls,
        store: TeamRunStore,
        team_run_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> "TeamRun":
        """Rehydrate a TeamRun from its durable event log.

        Replays every event emitted for ``team_run_id`` back into a
        fresh set of runtime objects:

        * ``Dispatcher.graph`` is reconstructed from ``work_item_added``
          events plus the final ``work_item_status`` seen for each id.
        * ``InMemoryArtifactStore`` is repopulated from
          ``artifact_written`` events.
        * ``BudgetState`` is set from the last ``budget_update`` event
          (fallback: counted from graph + artifact sizes).
        * The ready queue is rebuilt to hold every WorkItem that ended
          up in ``READY`` status at the end of the log.

        The returned TeamRun is **paused**: no executors are running.
        Callers resume it explicitly via ``TeamRun.resume(...)`` so they
        can decide whether to finish the run, inspect it, or cancel.

        Raises ``ValueError`` if no events exist for ``team_run_id`` or
        the log lacks a ``team_run_created`` header.
        """
        events = store.load_run(team_run_id)
        if not events:
            raise ValueError(f"no events for team_run_id={team_run_id!r}")

        if checkpoint_id is not None:
            checkpoint_event = next(
                (
                    ev
                    for ev in events
                    if ev.kind == "checkpoint_taken"
                    and str(ev.data.get("checkpoint_id") or "") == checkpoint_id
                ),
                None,
            )
            if checkpoint_event is None:
                raise ValueError(
                    f"checkpoint_id={checkpoint_id!r} not found for team_run_id={team_run_id!r}"
                )
            events = [ev for ev in events if ev.seq <= checkpoint_event.seq]

        created = next((e for e in events if e.kind == "team_run_created"), None)
        if created is None:
            raise ValueError(
                f"event log for {team_run_id!r} missing team_run_created header"
            )

        # --- header -----------------------------------------------------
        meta = created.data
        budgets_dict = dict(meta.get("budgets") or {})
        # BudgetConfig has all-defaulted fields; filter unknown keys defensively
        valid_keys = set(BudgetConfig.__dataclass_fields__.keys())
        budgets = BudgetConfig(**{k: v for k, v in budgets_dict.items() if k in valid_keys})

        services = build_team_runtime_services(
            team_run_id=team_run_id,
            budgets=budgets,
            budget_state=BudgetState(),
            user_request=meta.get("user_request") or "",
            goal=meta.get("goal"),
            repo_root=meta.get("repo_root") or None,
            event_store=store,
        )
        run = cls(
            session_id=meta.get("session_id") or "",
            user_request=meta.get("user_request") or "",
            budgets=budgets,
            goal=meta.get("goal"),
            sandbox_id=meta.get("sandbox_id") or None,
            repo_root=meta.get("repo_root") or None,
            team_run_id=team_run_id,
            services=services,
        )

        # --- fold events into runtime state -----------------------------
        graph = services.dispatcher.graph
        last_budget: tuple[int, int, int] | None = None
        final_status: str | None = None
        root_id: str | None = None

        for ev in events:
            if ev.kind == "work_item_added":
                wi = _work_item_from_dict(ev.data["work_item"])
                graph[wi.id] = wi
                if wi.depth == 0 and root_id is None:
                    root_id = wi.id
            elif ev.kind == "work_item_status":
                wi = graph.get(ev.data["wi_id"])
                if wi is None:
                    continue
                wi.status = WorkItemStatus(ev.data["status"])
                for key in ("started_at", "finished_at"):
                    iso = ev.data.get(key)
                    if iso:
                        setattr(wi, key, datetime.fromisoformat(iso))
                if "agent_run_id" in ev.data:
                    wi.agent_run_id = ev.data["agent_run_id"]
                if "failure_reason" in ev.data:
                    wi.failure_reason = ev.data["failure_reason"]
                if "artifact_ref" in ev.data:
                    wi.artifact_ref = ev.data["artifact_ref"]
            elif ev.kind == "artifact_written":
                # Re-save through the store so size bookkeeping stays
                # consistent with the live path.
                try:
                    services.artifact_store.save(ev.data["wi_id"], ev.data["payload"])
                except Exception:
                    pass  # budget exceeded on replay — keep going
            elif ev.kind == "budget_update":
                last_budget = (
                    int(ev.data["work_items_used"]),
                    int(ev.data["artifact_bytes_used"]),
                    int(ev.data.get("replans_used") or 0),
                )
            elif ev.kind == "team_run_status":
                final_status = ev.data.get("status")

        if last_budget is not None:
            run.budget_state.work_items_used = last_budget[0]
            run.budget_state.artifact_bytes_used = last_budget[1]
            run.budget_state.replans_used = last_budget[2]
        else:
            run.budget_state.work_items_used = len(graph)

        # Rebuild ready queue from whatever ended up READY.
        for wi in graph.values():
            if wi.status == WorkItemStatus.READY:
                services.dispatcher._ready_queue.put_nowait(wi.id)
                services.dispatcher._ready_order.append(wi.id)

        run.root_work_item_id = root_id
        if final_status:
            try:
                run.status = TeamRunStatus(final_status)
            except ValueError:
                pass

        return run


def _work_item_from_dict(data: dict[str, Any]) -> WorkItem:
    """Inverse of :func:`team.persistence.events.work_item_to_dict`."""
    def _parse_dt(iso: str | None) -> datetime | None:
        return datetime.fromisoformat(iso) if iso else None

    return WorkItem(
        id=data["id"],
        team_run_id=data["team_run_id"],
        agent_name=data["agent_name"],
        status=WorkItemStatus(data["status"]),
        kind=WorkItemKind(data.get("kind", "atomic")),
        deps=list(data.get("deps") or []),
        parent_id=data.get("parent_id"),
        root_id=data.get("root_id") or "",
        agent_run_id=data.get("agent_run_id"),
        payload=dict(data.get("payload") or {}),
        artifact_ref=data.get("artifact_ref"),
        timeout_seconds=data.get("timeout_seconds"),
        depth=int(data.get("depth") or 0),
        local_id=data.get("local_id"),
        briefings=[Briefing(**b) for b in (data.get("briefings") or [])],
        dep_artifacts=[DependencyArtifact(**d) for d in (data.get("dep_artifacts") or [])],
        created_at=_parse_dt(data.get("created_at")) or datetime.now(),
        started_at=_parse_dt(data.get("started_at")),
        finished_at=_parse_dt(data.get("finished_at")),
        failure_reason=data.get("failure_reason"),
        retry_count=int(data.get("retry_count") or 0),
        max_retries=int(data.get("max_retries") or 2),
        replan_source_id=data.get("replan_source_id"),
    )
