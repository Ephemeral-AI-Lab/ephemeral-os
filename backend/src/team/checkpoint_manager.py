"""CheckpointManager — run-state snapshot and rollback management.

Extracted from TaskCenter. Owns in-memory checkpoint ring buffer,
checkpoint persistence, and rollback/restore logic.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from collections import deque
from typing import Any, Callable

from team.models import BudgetState, Task, _utcnow
from team.runtime.checkpoint import TeamRunCheckpoint

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages run-state snapshots for recovery and rollback."""

    def __init__(
        self,
        team_run_id: str,
        max_checkpoints: int = 10,
        checkpoint_store: Any = None,
    ) -> None:
        self._team_run_id = team_run_id
        self._checkpoints: deque[TeamRunCheckpoint] = deque(maxlen=max_checkpoints)
        self._checkpoint_seq = 0
        self._checkpoint_store = checkpoint_store
        self._lock = asyncio.Lock()

    async def checkpoint(
        self,
        label: str | None,
        project_context: Any,
        tasks: dict[str, Task],
        ready_queue_order: list[str],
        budget_state: BudgetState,
        emit_checkpoint_cb: Callable[[str, str, int, str | None], None] | None = None,
    ) -> TeamRunCheckpoint:
        async with self._lock:
            self._checkpoint_seq += 1
            cp = TeamRunCheckpoint(
                id=str(uuid.uuid4()),
                team_run_id=self._team_run_id,
                sequence=self._checkpoint_seq,
                taken_at=_utcnow(),
                label=label,
                tasks=copy.deepcopy(tasks),
                ready_queue_order=list(ready_queue_order),
                project_context=copy.deepcopy(project_context),
                budget_state=copy.deepcopy(budget_state),
            )
            self._checkpoints.append(cp)
            if self._checkpoint_store is not None and getattr(
                self._checkpoint_store, "initialized", False
            ):
                try:
                    await self._checkpoint_store.save(cp)
                except Exception:
                    logger.debug("Failed to persist checkpoint %s", cp.id, exc_info=True)
            if emit_checkpoint_cb:
                emit_checkpoint_cb(self._team_run_id, cp.id, cp.sequence, label)
            return cp

    def list_checkpoints(self) -> list[TeamRunCheckpoint]:
        return list(self._checkpoints)

    def _get_checkpoint(self, checkpoint_id: str) -> TeamRunCheckpoint | None:
        return next((cp for cp in self._checkpoints if cp.id == checkpoint_id), None)

    async def _get_checkpoint_with_fallback(self, checkpoint_id: str) -> TeamRunCheckpoint | None:
        cp = self._get_checkpoint(checkpoint_id)
        if cp is not None:
            return cp
        if self._checkpoint_store is not None and getattr(
            self._checkpoint_store, "initialized", False
        ):
            rec = await self._checkpoint_store.load_by_id(checkpoint_id, self._team_run_id)
            if rec is not None:
                return self._record_to_checkpoint(rec)
        return None

    @staticmethod
    def _record_to_checkpoint(rec: Any) -> TeamRunCheckpoint:
        from datetime import datetime

        tasks: dict[str, Task] = {}
        for tid, td in (rec.tasks or {}).items():
            for f in ("created_at", "started_at", "finished_at"):
                val = td.get(f)
                if isinstance(val, str) and val:
                    try:
                        td[f] = datetime.fromisoformat(val)
                    except ValueError:
                        td[f] = None
                elif not isinstance(val, datetime):
                    td[f] = None
            if "status" in td:
                from team.models import TaskStatus

                td["status"] = TaskStatus(td["status"])
            tasks[tid] = Task(**td)
        return TeamRunCheckpoint(
            id=rec.id,
            team_run_id=rec.team_run_id,
            sequence=rec.sequence,
            taken_at=rec.taken_at,
            label=rec.label,
            tasks=tasks,
            ready_queue_order=list(rec.ready_queue_order or []),
            project_context=rec.project_context,
            budget_state=BudgetState(**(rec.budget_state or {})),
        )

    async def rollback_to(
        self,
        checkpoint_id: str,
        project_context_setter: Callable[[Any], None],
        replace_run_tasks_fn: Callable[[list[Task]], Any],
        get_record_fn: Callable[[str], Any] | None = None,
    ) -> TeamRunCheckpoint | None:
        cp = await self._get_checkpoint_with_fallback(checkpoint_id)
        if cp is None:
            return None
        await replace_run_tasks_fn(list(cp.tasks.values()))
        project_context_setter(copy.deepcopy(cp.project_context))
        return cp

    async def prepare_for_resume(
        self,
        resume_snapshot: list[Task] | None,
        recover_running_fn: Callable[[], Any],
        replace_run_tasks_fn: Callable[[list[Task]], Any],
    ) -> None:
        if resume_snapshot is not None:
            await replace_run_tasks_fn(resume_snapshot)
        recovered = await recover_running_fn()
        if recovered:
            logger.info("Recovered %d running tasks to ready", len(recovered))
