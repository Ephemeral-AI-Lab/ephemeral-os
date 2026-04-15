"""BlockerStore — durable persistence for blocker records."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from team.models import Blocker, BlockerStatus
from team.persistence.blocker_record import BlockerRecord

logger = logging.getLogger(__name__)


class BlockerStore:
    """CRUD operations for blockers, backed by the ``blockers`` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], team_run_id: str) -> None:
        self._sf = session_factory
        self._team_run_id = team_run_id

    async def save(self, blocker: Blocker) -> None:
        """Insert or update a blocker record."""
        async with self._sf() as db:
            existing = await db.get(BlockerRecord, (blocker.id, self._team_run_id))
            if existing is not None:
                existing.status = blocker.status.value
                existing.reason = blocker.reason
                existing.root_cause_paths = blocker.root_cause_paths
                existing.suggestion = blocker.suggestion
                existing.fix_task_id = blocker.fix_task_id
                existing.declared_by = blocker.declared_by
                existing.fix_summary = blocker.fix_summary
                existing.pending_assessments = blocker.pending_assessments
                existing.resolved_at = blocker.resolved_at
            else:
                db.add(BlockerRecord(
                    id=blocker.id,
                    team_run_id=self._team_run_id,
                    status=blocker.status.value,
                    reason=blocker.reason,
                    root_cause_paths=blocker.root_cause_paths,
                    initiating_task_id=blocker.initiating_task_id,
                    suggestion=blocker.suggestion,
                    fix_task_id=blocker.fix_task_id,
                    declared_by=blocker.declared_by,
                    fix_summary=blocker.fix_summary,
                    pending_assessments=blocker.pending_assessments,
                    created_at=blocker.created_at,
                    resolved_at=blocker.resolved_at,
                ))
            await db.commit()

    async def load_active(self) -> list[Blocker]:
        """Load all non-terminal blockers for this team run."""
        async with self._sf() as db:
            stmt = (
                select(BlockerRecord)
                .where(
                    BlockerRecord.team_run_id == self._team_run_id,
                    BlockerRecord.status.notin_(("resolved", "failed")),
                )
                .order_by(BlockerRecord.created_at)
            )
            rows = (await db.execute(stmt)).scalars().all()
            return [self._record_to_blocker(rec) for rec in rows]

    @staticmethod
    def _record_to_blocker(rec: BlockerRecord) -> Blocker:
        return Blocker(
            id=rec.id,
            team_run_id=rec.team_run_id,
            status=BlockerStatus(rec.status),
            reason=rec.reason,
            root_cause_paths=list(rec.root_cause_paths) if rec.root_cause_paths else [],
            initiating_task_id=rec.initiating_task_id,
            suggestion=rec.suggestion,
            fix_task_id=rec.fix_task_id,
            declared_by=rec.declared_by,
            fix_summary=rec.fix_summary,
            pending_assessments=rec.pending_assessments,
            created_at=rec.created_at,
            resolved_at=rec.resolved_at,
        )
