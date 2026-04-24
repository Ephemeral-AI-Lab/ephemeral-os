"""Task SQL — ORM model + raw query/mutation functions for ``tasks``.

This module is the minimal CRUD surface used by
:class:`team.persistence.task_store.TaskStore`. All mutation *rules*
(dependent promotion, cascade, parent promotion, replanner spawning) live in
:class:`team.runtime.task_graph.TaskGraph` and reach this layer as
pre-computed ``GraphMutation`` fragments flushed via ``TaskStore.persist``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast as type_cast

from sqlalchemy import (
    DateTime,
    Integer,
    JSON,
    Text,
    cast,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from team.core.errors import GraphInvariantViolation
from team.core.models import TERMINAL_STATUSES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskRecord(Base):
    """Durable record of a team task. Partitioned by ``team_run_id``."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    deps: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    scope_paths: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    scope_ltree: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    parent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_id: Mapped[str] = mapped_column(Text, default="")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    agent_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fired_by_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)


_TERMINAL = [s.value for s in TERMINAL_STATUSES]
_TERMINAL_ON_SET = {"done", "failed", "cancelled", "request_replan"}


# ---- reads --------------------------------------------------------------


async def fetch_record(db: AsyncSession, team_run_id: str, task_id: str) -> TaskRecord | None:
    return (await db.execute(
        select(TaskRecord).where(
            TaskRecord.id == task_id, TaskRecord.team_run_id == team_run_id
        )
    )).scalar_one_or_none()


async def fetch_all_records(db: AsyncSession, team_run_id: str) -> list[TaskRecord]:
    return list((await db.execute(
        select(TaskRecord)
        .where(TaskRecord.team_run_id == team_run_id)
        .order_by(TaskRecord.depth, TaskRecord.created_at)
    )).scalars().all())


async def count_non_terminal(db: AsyncSession, team_run_id: str) -> int:
    return int((await db.execute(
        select(func.count()).where(
            TaskRecord.team_run_id == team_run_id,
            TaskRecord.status.notin_(_TERMINAL),
        )
    )).scalar() or 0)


async def fetch_unsatisfied_dep_ids(
    db: AsyncSession, team_run_id: str, dep_ids: list[str]
) -> list[str]:
    """Return the subset of ``dep_ids`` whose rows are not yet DONE.

    Called only by :func:`mark_running` to guard the lockless worker claim
    against a dependency that flipped back to non-DONE between READY
    promotion and claim time.
    """
    if not dep_ids:
        return []
    rows = (await db.execute(
        select(TaskRecord.id, TaskRecord.status).where(
            TaskRecord.team_run_id == team_run_id, TaskRecord.id.in_(set(dep_ids))
        )
    )).all()
    statuses = {str(r.id): str(r.status) for r in rows}
    return [d for d in dep_ids if statuses.get(d) != "done"]


# ---- writes -------------------------------------------------------------


async def set_status(
    db: AsyncSession,
    team_run_id: str,
    task_id: str,
    status: str,
    reason: str | None = None,
) -> None:
    """Transition to ``status``; terminal statuses stamp ``finished_at``,
    ``reason`` populates ``failure_reason`` (prefixed for ``request_replan``
    to match the in-memory ``TaskGraph.apply`` rendering)."""
    values: dict[str, Any] = {"status": status}
    if status in _TERMINAL_ON_SET:
        values["finished_at"] = func.now()
    if reason is not None:
        values["failure_reason"] = (
            f"replan_requested: {reason}" if status == "request_replan" else reason
        )
    await db.execute(
        update(TaskRecord)
        .where(TaskRecord.id == task_id, TaskRecord.team_run_id == team_run_id)
        .values(**values)
    )


async def set_failure_reason(
    db: AsyncSession, team_run_id: str, task_id: str, failure_reason: str
) -> None:
    """Update ``failure_reason`` without touching status. Flushes
    ``FailureReasonPatch`` mutations from ``TaskGraph.finalize_replanned_origin``."""
    await db.execute(
        update(TaskRecord)
        .where(TaskRecord.team_run_id == team_run_id, TaskRecord.id == task_id)
        .values(failure_reason=failure_reason)
    )


async def replace_dependency(
    db: AsyncSession, team_run_id: str, *, old_dep_id: str, new_dep_ids: list[str]
) -> list[str]:
    """Rewire every task's deps from ``old_dep_id`` to ``new_dep_ids``.

    Keeps the defense-in-depth invariant check (raises
    ``GraphInvariantViolation`` if any dependent is non-pending) — the
    ``TaskGraph`` has already enforced this in memory, but if graph and DB
    have drifted, this surfaces the drift instead of silently corrupting state.
    """
    violations = (await db.execute(
        select(TaskRecord.id, TaskRecord.status).where(
            TaskRecord.team_run_id == team_run_id,
            TaskRecord.deps.contains([old_dep_id]),
            TaskRecord.status != "pending",
            TaskRecord.status != "cancelled",
        )
    )).all()
    if violations:
        details = ", ".join(f"{r.id}:{r.status}" for r in violations)
        raise GraphInvariantViolation(
            "replan dependency invariant violated: "
            f"tasks depending on {old_dep_id!r} must be pending; found {details}"
        )
    updated_deps = func.array_cat(
        func.array_remove(TaskRecord.deps, old_dep_id),
        cast(new_dep_ids, ARRAY(Text)),
    )
    result = await db.execute(
        update(TaskRecord)
        .where(
            TaskRecord.team_run_id == team_run_id,
            TaskRecord.deps.contains([old_dep_id]),
            TaskRecord.status == "pending",
        )
        .values(deps=updated_deps, started_at=None, agent_run_id=None)
        .returning(TaskRecord.id)
        .execution_options(synchronize_session=False)
    )
    return [str(r.id) for r in result.fetchall()]


async def bulk_cancel(
    db: AsyncSession,
    team_run_id: str,
    *,
    statuses: tuple[str, ...] | None = None,
    task_ids: list[str] | None = None,
    reason: str,
) -> int:
    """Cancel by current ``statuses`` or by ``task_ids`` (non-terminal only). Returns rowcount."""
    conditions = [TaskRecord.team_run_id == team_run_id]
    if statuses is not None:
        conditions.append(TaskRecord.status.in_(statuses))
    if task_ids is not None:
        if not task_ids:
            return 0
        conditions.extend([TaskRecord.id.in_(task_ids), TaskRecord.status.notin_(_TERMINAL)])
    result = await db.execute(
        update(TaskRecord).where(*conditions).values(
            status="cancelled", finished_at=func.now(), failure_reason=reason
        )
    )
    return int(type_cast(CursorResult[Any], result).rowcount or 0)


async def mark_running(
    db: AsyncSession, team_run_id: str, task_id: str, agent_run_id: str
) -> TaskRecord | None:
    """Atomically claim a READY task (or re-claim an already-RUNNING one).

    This is the one lockless DB-atomic operation — multiple workers race
    through this path and the DB UPDATE is the arbiter.
    """
    return (await db.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.team_run_id == team_run_id,
            TaskRecord.status.in_(("ready", "running")),
        )
        .values(
            status="running",
            agent_run_id=agent_run_id,
            started_at=func.coalesce(TaskRecord.started_at, func.now()),
        )
        .returning(TaskRecord)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()


async def insert_task_record(db: AsyncSession, record: TaskRecord) -> None:
    db.add(record)
    await db.flush()
