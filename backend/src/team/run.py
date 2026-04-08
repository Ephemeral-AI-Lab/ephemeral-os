"""TeamRun lifecycle container."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from team.artifact_store import InMemoryArtifactStore
from team.checkpoint import CheckpointStore, TeamRunCheckpoint
from team.context.files import ChangeLog, register_team_run, unregister_team_run
from team.context.project import ProjectContext
from team.dispatcher import Dispatcher
from team.types import (
    BudgetConfig,
    BudgetState,
    TeamRunStatus,
    WorkItem,
    WorkItemStatus,
)
from team.worker import Worker


class TeamRun:
    def __init__(
        self,
        *,
        session_id: str,
        user_request: str,
        budgets: BudgetConfig | None = None,
        goal: str | None = None,
    ) -> None:
        self.id = str(uuid.uuid4())
        self.session_id = session_id
        self.user_request = user_request
        self.budgets = budgets or BudgetConfig()
        self.budget_state = BudgetState()
        self.status = TeamRunStatus.PENDING
        self.project_context = ProjectContext(
            goal=goal or user_request, user_request=user_request
        )
        self.change_log = ChangeLog()
        self.artifacts = InMemoryArtifactStore(self.budgets, self.budget_state)
        self.dispatcher = Dispatcher(
            team_run_id=self.id,
            budgets=self.budgets,
            budget_state=self.budget_state,
            artifact_store=self.artifacts,
            checkpoint_store=CheckpointStore(),
        )
        self.cancel_event = asyncio.Event()
        self.root_work_item_id: str | None = None
        self._worker_tasks: list[asyncio.Task] = []

    # ---- lifecycle -------------------------------------------------------

    async def start(
        self,
        agent_name: str,
        payload: dict[str, Any],
        *,
        worker_factory: Callable[["TeamRun"], Worker],
        num_workers: int = 1,
    ) -> None:
        root = WorkItem(
            id=str(uuid.uuid4()),
            team_run_id=self.id,
            agent_name=agent_name,
            status=WorkItemStatus.PENDING,
            payload=dict(payload),
            depth=0,
        )
        root.root_id = root.id
        self.root_work_item_id = root.id
        await self.dispatcher.add_work_item(root)
        self.status = TeamRunStatus.RUNNING
        register_team_run(self)

        for _ in range(num_workers):
            worker = worker_factory(self)
            self._worker_tasks.append(asyncio.create_task(worker.run_forever()))

    async def wait(self) -> TeamRunStatus:
        # Completion == all WorkItems in this TeamRun are terminal.
        while True:
            if self.dispatcher.all_terminal():
                break
            await asyncio.sleep(0.05)
        await self._drain_workers()
        self._compute_final_status()
        unregister_team_run(self.id)
        return self.status

    async def _drain_workers(self) -> None:
        # Signal workers, then await their natural exit.
        self.cancel_event.set()
        for t in self._worker_tasks:
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        self._worker_tasks = []
        self.cancel_event.clear()

    def _compute_final_status(self) -> None:
        statuses = {wi.status for wi in self.dispatcher.graph.values()}
        if WorkItemStatus.FAILED in statuses:
            self.status = TeamRunStatus.FAILED
        elif WorkItemStatus.CANCELLED in statuses and self.status == TeamRunStatus.RUNNING:
            self.status = TeamRunStatus.CANCELLED if all(
                s in (WorkItemStatus.CANCELLED, WorkItemStatus.DONE) for s in statuses
            ) else TeamRunStatus.FAILED
        else:
            self.status = TeamRunStatus.SUCCEEDED

    async def cancel(self) -> None:
        self.cancel_event.set()
        await self.dispatcher.cancel_all_pending()

    # ---- checkpoint API --------------------------------------------------

    async def checkpoint(self, label: str | None = None) -> str:
        cp = await self.dispatcher.checkpoint(
            label=label,
            project_context=self.project_context,
            change_log_entries=self.change_log.all(),
        )
        return cp.id

    def list_checkpoints(self) -> list[TeamRunCheckpoint]:
        return self.dispatcher.checkpoint_store.list()

    async def rollback_to(self, checkpoint_id: str) -> None:
        # Phase 1 — cooperative drain.
        self.cancel_event.set()
        await self._drain_workers()
        # Phase 2 — atomic restore.
        await self.dispatcher.rollback_to(
            checkpoint_id,
            project_context_setter=lambda pc: setattr(self, "project_context", pc),
            change_log_setter=lambda entries: self.change_log.restore(entries),
        )
        self.cancel_event.clear()

    async def delete_checkpoint(self, checkpoint_id: str) -> bool:
        return await self.dispatcher.delete_checkpoint(checkpoint_id)
