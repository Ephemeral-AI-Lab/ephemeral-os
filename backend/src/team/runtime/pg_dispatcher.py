"""PGDispatcher — PostgreSQL-backed work queue for team coordination.

Replaces the in-memory Dispatcher's DAG/queue with PostgreSQL queries.
Uses ``FOR UPDATE SKIP LOCKED`` for atomic, lock-free task claiming and
``pending_dep_count`` for dependency tracking without DAG traversal.

See Section 14.6 of the coordination redesign doc.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from team.models import TaskSpec
from team.persistence.ltree_utils import path_to_ltree
from team.persistence.task_record import TaskRecord

logger = logging.getLogger(__name__)


class PGDispatcher:
    """Dispatcher backed by PostgreSQL. No in-memory DAG state.

    Uses async_sessionmaker from the team engine (Section 14.2).
    ORM for standard CRUD, text() for PG-specific atomic operations.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def pop_ready(self, run_id: str) -> TaskRecord | None:
        """Atomically claim the next ready task. Lock-free under concurrency.

        Uses ``FOR UPDATE SKIP LOCKED`` — a purpose-built work queue
        primitive with no ORM equivalent.
        """
        async with self._sf() as db:
            row = (
                await db.execute(
                    text("""
                        UPDATE tasks SET status = 'running', started_at = NOW()
                        WHERE (id, team_run_id) = (
                            SELECT t.id, t.team_run_id FROM tasks t
                            WHERE t.team_run_id = :run_id
                              AND t.status = 'ready'
                              AND t.pending_dep_count = 0
                            ORDER BY t.depth, t.created_at
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        RETURNING id, team_run_id, agent_name, status, task,
                                  deps, scope_paths, scope_ltree,
                                  cascade_policy, parent_id, root_id, depth,
                                  pending_dep_count, retry_count, max_retries,
                                  agent_run_id, created_at, started_at,
                                  finished_at, failure_reason
                    """),
                    {"run_id": run_id},
                )
            ).fetchone()
            await db.commit()
            return _row_to_record(row) if row else None

    async def mark_done(self, task_id: str, run_id: str) -> list[str]:
        """Mark task done, decrement dependents' pending_dep_count, promote.

        Returns IDs of tasks promoted to 'ready' (pending_dep_count hit 0).
        """
        async with self._sf() as db:
            # 1. Mark the task done
            await db.execute(
                text(
                    "UPDATE tasks SET status = 'done', finished_at = NOW() "
                    "WHERE id = :task_id AND team_run_id = :run_id"
                ),
                {"task_id": task_id, "run_id": run_id},
            )

            # 2. Decrement pending_dep_count for dependents and promote
            promoted = (
                await db.execute(
                    text("""
                        UPDATE tasks t
                        SET pending_dep_count = pending_dep_count - 1,
                            status = CASE
                                WHEN pending_dep_count - 1 = 0 THEN 'ready'
                                ELSE status
                            END
                        WHERE t.team_run_id = :run_id
                          AND t.status = 'pending'
                          AND :task_id = ANY(t.deps)
                          AND t.pending_dep_count > 0
                        RETURNING CASE
                            WHEN pending_dep_count = 0 THEN t.id
                            ELSE NULL
                        END AS promoted_id
                    """),
                    {"run_id": run_id, "task_id": task_id},
                )
            ).fetchall()
            await db.commit()

            return [
                r.promoted_id for r in promoted if r.promoted_id is not None
            ]

    async def mark_failed(
        self,
        task_id: str,
        run_id: str,
        reason: str,
    ) -> None:
        """Mark a task as failed with a reason."""
        async with self._sf() as db:
            await db.execute(
                text(
                    "UPDATE tasks SET status = 'failed', finished_at = NOW(), "
                    "failure_reason = :reason "
                    "WHERE id = :task_id AND team_run_id = :run_id"
                ),
                {"task_id": task_id, "run_id": run_id, "reason": reason},
            )
            await db.commit()

    async def mark_cancelled(
        self,
        task_id: str,
        run_id: str,
        reason: str,
    ) -> None:
        """Mark a task as cancelled."""
        async with self._sf() as db:
            await db.execute(
                text(
                    "UPDATE tasks SET status = 'cancelled', finished_at = NOW(), "
                    "failure_reason = :reason "
                    "WHERE id = :task_id AND team_run_id = :run_id"
                ),
                {"task_id": task_id, "run_id": run_id, "reason": reason},
            )
            await db.commit()

    async def insert_plan(
        self,
        run_id: str,
        tasks: list[TaskSpec],
        parent_id: str | None = None,
        parent_depth: int = 0,
        parent_root_id: str | None = None,
    ) -> list[TaskRecord]:
        """Insert plan tasks atomically via ORM bulk insert.

        Root tasks (no deps) start as 'ready'; others as 'pending'.
        After insertion, a catch-up pass decrements pending_dep_count
        for any deps that are already done.

        Returns the inserted TaskRecord objects.
        """
        async with self._sf() as db:
            records: list[TaskRecord] = []
            for spec in tasks:
                status = "ready" if not spec.deps else "pending"
                root_id = parent_root_id if parent_id else spec.id
                records.append(
                    TaskRecord(
                        id=spec.id,
                        team_run_id=run_id,
                        agent_name=spec.agent,
                        status=status,
                        task=spec.task,
                        deps=list(spec.deps),
                        scope_paths=list(spec.scope_paths),
                        scope_ltree=[
                            path_to_ltree(p) for p in spec.scope_paths
                        ],
                        parent_id=parent_id,
                        root_id=root_id or "",
                        depth=(parent_depth + 1) if parent_id else 0,
                        pending_dep_count=len(spec.deps),
                    )
                )
            db.add_all(records)
            await db.flush()  # IDs visible for catch-up query

            # Catch-up: decrement pending_dep_count for deps already done.
            await db.execute(
                text("""
                    WITH already_done AS (
                        SELECT id FROM tasks
                        WHERE team_run_id = :run_id AND status = 'done'
                    )
                    UPDATE tasks t
                    SET pending_dep_count = pending_dep_count - (
                            SELECT COUNT(*) FROM already_done ad
                            WHERE ad.id = ANY(t.deps)
                        ),
                        status = CASE
                            WHEN pending_dep_count - (
                                SELECT COUNT(*) FROM already_done ad
                                WHERE ad.id = ANY(t.deps)
                            ) = 0 THEN 'ready'
                            ELSE status
                        END
                    WHERE t.team_run_id = :run_id
                      AND t.status = 'pending'
                      AND t.deps && (SELECT array_agg(id) FROM already_done)
                """),
                {"run_id": run_id},
            )
            await db.commit()
            return records

    async def get_task(self, task_id: str, run_id: str) -> TaskRecord | None:
        """Fetch a single task by ID."""
        async with self._sf() as db:
            stmt = select(TaskRecord).where(
                TaskRecord.id == task_id,
                TaskRecord.team_run_id == run_id,
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def get_tasks_by_status(
        self,
        run_id: str,
        status: str,
    ) -> list[TaskRecord]:
        """Fetch all tasks with a given status."""
        async with self._sf() as db:
            stmt = (
                select(TaskRecord)
                .where(
                    TaskRecord.team_run_id == run_id,
                    TaskRecord.status == status,
                )
                .order_by(TaskRecord.depth, TaskRecord.created_at)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def all_terminal(self, run_id: str) -> bool:
        """Check if all tasks in the run are in a terminal state."""
        async with self._sf() as db:
            result = await db.execute(
                text("""
                    SELECT COUNT(*) FROM tasks
                    WHERE team_run_id = :run_id
                      AND status NOT IN ('done', 'failed', 'cancelled')
                """),
                {"run_id": run_id},
            )
            count = result.scalar()
            return count == 0

    async def cascade_cancel(
        self,
        run_id: str,
        failed_task_id: str,
    ) -> list[str]:
        """Cancel all pending/ready tasks that transitively depend on failed_task_id.

        Returns IDs of cancelled tasks.
        """
        async with self._sf() as db:
            result = await db.execute(
                text("""
                    UPDATE tasks
                    SET status = 'cancelled',
                        finished_at = NOW(),
                        failure_reason = :reason
                    WHERE team_run_id = :run_id
                      AND status IN ('pending', 'ready')
                      AND :task_id = ANY(deps)
                    RETURNING id
                """),
                {
                    "run_id": run_id,
                    "task_id": failed_task_id,
                    "reason": f"cascade_cancel: dependency {failed_task_id} failed",
                },
            )
            cancelled = [r.id for r in result.fetchall()]
            await db.commit()
            return cancelled

    async def recover_running(self, run_id: str) -> list[TaskRecord]:
        """Crash recovery: reset 'running' tasks back to 'ready'.

        Called on startup to recover tasks that were running when the
        process crashed. Returns the reset tasks.
        """
        async with self._sf() as db:
            result = await db.execute(
                text("""
                    UPDATE tasks
                    SET status = 'ready', started_at = NULL, agent_run_id = NULL
                    WHERE team_run_id = :run_id AND status = 'running'
                    RETURNING id, team_run_id, agent_name, status, task,
                              deps, scope_paths, scope_ltree,
                              cascade_policy, parent_id, root_id, depth,
                              pending_dep_count, retry_count, max_retries,
                              agent_run_id, created_at, started_at,
                              finished_at, failure_reason
                """),
                {"run_id": run_id},
            )
            rows = result.fetchall()
            await db.commit()
            return [_row_to_record(r) for r in rows]


def _row_to_record(row: Any) -> TaskRecord:
    """Convert a raw SQL row to a TaskRecord ORM instance."""
    return TaskRecord(
        id=row.id,
        team_run_id=row.team_run_id,
        agent_name=row.agent_name,
        status=row.status,
        task=row.task,
        deps=list(row.deps) if row.deps else [],
        scope_paths=list(row.scope_paths) if row.scope_paths else [],
        scope_ltree=list(row.scope_ltree) if row.scope_ltree else [],
        cascade_policy=row.cascade_policy,
        parent_id=row.parent_id,
        root_id=row.root_id or "",
        depth=row.depth,
        pending_dep_count=row.pending_dep_count,
        retry_count=row.retry_count,
        max_retries=row.max_retries,
        agent_run_id=row.agent_run_id,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        failure_reason=row.failure_reason,
    )
