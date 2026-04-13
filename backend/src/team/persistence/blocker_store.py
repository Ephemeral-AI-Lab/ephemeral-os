"""BlockerStore — durable persistence for blocker records."""

from __future__ import annotations

import logging
import time

from sqlalchemy import text
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
            result = await db.execute(text(
                "SELECT * FROM blockers "
                "WHERE team_run_id = :rid AND status NOT IN ('resolved', 'failed') "
                "ORDER BY created_at"
            ), {"rid": self._team_run_id})
            return [self._row_to_blocker(row) for row in result.fetchall()]

    @staticmethod
    def _row_to_blocker(row) -> Blocker:
        return Blocker(
            id=row.id,
            team_run_id=row.team_run_id,
            status=BlockerStatus(row.status),
            reason=row.reason,
            root_cause_paths=list(row.root_cause_paths) if row.root_cause_paths else [],
            initiating_task_id=row.initiating_task_id,
            fix_task_id=row.fix_task_id,
            declared_by=row.declared_by,
            fix_summary=row.fix_summary,
            pending_assessments=row.pending_assessments,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
        )
