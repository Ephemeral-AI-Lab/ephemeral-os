"""FileChangeStore — durable file-change persistence for cross-run history.

Dual-write companion to the in-memory Ledger. The Ledger handles hot-path
reads (context_for, same-run scope queries). This store handles:

  1. Cross-run contention history (query_edit_history for planner)
  2. Multi-process visibility (edits by process A visible to process B)
  3. Crash recovery (edit history survives process restart)

Follows the existing Store pattern (TeamDefinitionStore, etc.):
sync SQLAlchemy sessionmaker, initialize() contract, null fallback.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, Text, text
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from db.base import Base
from db.stores.base import SyncStoreMixin
from team.persistence.ltree_utils import path_to_ltree
from team.persistence.pg_types import LTREE

logger = logging.getLogger(__name__)

_FC_SELECT = (
    "SELECT id, team_run_id, file_path, agent_id, agent_run_id,"
    " path_ltree, edit_type, old_hash, new_hash,"
    " description, created_at FROM file_changes"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# ORM Model
# ---------------------------------------------------------------------------


class FileChangeRecord(Base):
    """Durable record of a file edit by an agent."""

    __tablename__ = "file_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_run_id: Mapped[str] = mapped_column(String(64), index=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    path_ltree: Mapped[str] = mapped_column(LTREE(), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_run_id: Mapped[str] = mapped_column(String(64), default="")
    edit_type: Mapped[str] = mapped_column(String(32), default="edit")
    old_hash: Mapped[str] = mapped_column(String(64), default="")
    new_hash: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<FileChangeRecord {self.file_path!r} "
            f"by={self.agent_id!r} type={self.edit_type!r}>"
        )


# ---------------------------------------------------------------------------
# Contention hotspot result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentionHotspot:
    file_path: str
    agent_count: int
    edit_count: int


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FileChangeStore(SyncStoreMixin):
    """Durable file-change persistence. Sync SQLAlchemy, existing Store pattern."""

    def record(
        self,
        *,
        team_run_id: str,
        file_path: str,
        agent_id: str,
        agent_run_id: str = "",
        edit_type: str = "edit",
        old_hash: str = "",
        new_hash: str = "",
        description: str = "",
    ) -> FileChangeRecord:
        """Insert a file change record."""
        record = FileChangeRecord(
            team_run_id=team_run_id,
            file_path=file_path,
            path_ltree=path_to_ltree(file_path),
            agent_id=agent_id,
            agent_run_id=agent_run_id,
            edit_type=edit_type,
            old_hash=old_hash,
            new_hash=new_hash,
            description=description,
        )
        with self._sf() as session:
            session.add(record)
            session.commit()
        return record

    def changes_in_scope(
        self,
        team_run_id: str,
        scope_prefixes: list[str],
        since: float,
    ) -> list[FileChangeRecord]:
        """Return file changes under scope prefixes since a timestamp."""
        if not scope_prefixes:
            return []
        with self._sf() as session:
            params: dict[str, Any] = {
                "run_id": team_run_id,
                "since": datetime.fromtimestamp(since, tz=timezone.utc),
                "scopes": [path_to_ltree(prefix.rstrip("/")) for prefix in scope_prefixes],
            }

            result = session.execute(
                text(
                    f"{_FC_SELECT}"
                    " WHERE team_run_id = :run_id"
                    " AND created_at > :since"
                    " AND path_ltree <@ ANY(:scopes::ltree[])"
                    " ORDER BY created_at DESC"
                ),
                params,
            )
            return [self._row_to_record(row) for row in result.fetchall()]

    def external_changes_in_scope(
        self,
        team_run_id: str,
        scope_prefixes: list[str],
        since: float,
        exclude_run_id: str | None = None,
    ) -> list[FileChangeRecord]:
        """Return changes in scope NOT made by agents in this team run."""
        changes = self.changes_in_scope(team_run_id, scope_prefixes, since)
        if exclude_run_id:
            changes = [c for c in changes if c.agent_run_id != exclude_run_id]
        return changes

    def changes_since(
        self,
        since: float,
        team_run_id: str | None = None,
    ) -> list[FileChangeRecord]:
        """Return all file changes after *since* (epoch float)."""
        where = "WHERE created_at > :since"
        params: dict[str, Any] = {
            "since": datetime.fromtimestamp(since, tz=timezone.utc),
        }
        if team_run_id is not None:
            where += " AND team_run_id = :run_id"
            params["run_id"] = team_run_id
        with self._sf() as session:
            result = session.execute(
                text(f"{_FC_SELECT} {where} ORDER BY created_at ASC"),
                params,
            )
            return [self._row_to_record(row) for row in result.fetchall()]

    def recent_edits(
        self,
        seconds: float = 60.0,
        team_run_id: str | None = None,
    ) -> list[FileChangeRecord]:
        """Return all file changes in the last *seconds*."""
        since = time.time() - seconds
        return self.changes_since(since, team_run_id=team_run_id)

    def hotspots(
        self,
        limit: int = 10,
        team_run_id: str | None = None,
    ) -> list[tuple[str, int]]:
        """Return top files by edit count."""
        where = "WHERE team_run_id = :run_id" if team_run_id else ""
        params: dict[str, Any] = {"lim": limit}
        if team_run_id is not None:
            params["run_id"] = team_run_id
        with self._sf() as session:
            result = session.execute(
                text(
                    f"SELECT file_path, COUNT(*) AS edit_count"
                    f" FROM file_changes {where}"
                    f" GROUP BY file_path ORDER BY edit_count DESC LIMIT :lim"
                ),
                params,
            )
            return [(row.file_path, row.edit_count) for row in result.fetchall()]

    def who_changed(
        self,
        file_path: str,
        team_run_id: str | None = None,
    ) -> list[FileChangeRecord]:
        """Return all edit records for a specific file."""
        where = "WHERE file_path = :fp"
        params: dict[str, Any] = {"fp": file_path}
        if team_run_id is not None:
            where += " AND team_run_id = :run_id"
            params["run_id"] = team_run_id
        with self._sf() as session:
            result = session.execute(
                text(f"{_FC_SELECT} {where} ORDER BY created_at ASC"),
                params,
            )
            return [self._row_to_record(row) for row in result.fetchall()]

    def changes_by_agent_run(
        self,
        team_run_id: str,
        agent_run_id: str,
    ) -> list[FileChangeRecord]:
        """Return all file changes made by a specific agent run."""
        if not agent_run_id:
            return []
        with self._sf() as session:
            result = session.execute(
                text(
                    f"{_FC_SELECT}"
                    " WHERE team_run_id = :run_id"
                    " AND agent_run_id = :agent_run_id"
                    " ORDER BY created_at ASC"
                ),
                {"run_id": team_run_id, "agent_run_id": agent_run_id},
            )
            return [self._row_to_record(row) for row in result.fetchall()]

    def contention_hotspots(
        self,
        scope_prefixes: list[str],
        limit: int = 10,
        days: int = 7,
    ) -> list[ContentionHotspot]:
        """Cross-run contention hotspots: files edited by many agents.

        Used by planner's query_edit_history tool to predict conflicts.
        Only considers edits from the last *days* to avoid full table scans.
        """
        if not scope_prefixes:
            return []
        with self._sf() as session:
            params: dict[str, Any] = {
                "lim": limit,
                "scopes": [path_to_ltree(prefix.rstrip("/")) for prefix in scope_prefixes],
                "cutoff": _utcnow() - timedelta(days=days),
            }

            result = session.execute(
                text("""
                    SELECT file_path,
                           COUNT(DISTINCT agent_id) AS agent_count,
                           COUNT(*) AS edit_count
                    FROM file_changes
                    WHERE path_ltree <@ ANY(:scopes::ltree[])
                      AND created_at > :cutoff
                    GROUP BY file_path
                    HAVING COUNT(DISTINCT agent_id) > 1
                    ORDER BY agent_count DESC, edit_count DESC
                    LIMIT :lim
                """),
                params,
            )
            return [
                ContentionHotspot(
                    file_path=row.file_path,
                    agent_count=row.agent_count,
                    edit_count=row.edit_count,
                )
                for row in result.fetchall()
            ]

    @staticmethod
    def _row_to_record(row: Any) -> FileChangeRecord:
        """Convert a raw SQL row to a FileChangeRecord."""
        return FileChangeRecord(
            id=row.id,
            team_run_id=row.team_run_id,
            file_path=row.file_path,
            path_ltree=row.path_ltree,
            agent_id=row.agent_id,
            agent_run_id=getattr(row, "agent_run_id", ""),
            edit_type=row.edit_type,
            old_hash=getattr(row, "old_hash", ""),
            new_hash=getattr(row, "new_hash", ""),
            description=getattr(row, "description", ""),
            created_at=row.created_at,
        )


# ---------------------------------------------------------------------------
# Null fallback (no-PG / tests)
# ---------------------------------------------------------------------------


class NullFileChangeStore:
    """No-op store for when PostgreSQL is unavailable."""

    initialized: bool = False

    def record(self, **kwargs: Any) -> None:
        pass

    def changes_since(self, *args: Any, **kwargs: Any) -> list:
        return []

    def recent_edits(self, *args: Any, **kwargs: Any) -> list:
        return []

    def hotspots(self, *args: Any, **kwargs: Any) -> list:
        return []

    def who_changed(self, *args: Any, **kwargs: Any) -> list:
        return []

    def changes_in_scope(self, *args: Any, **kwargs: Any) -> list:
        return []

    def external_changes_in_scope(self, *args: Any, **kwargs: Any) -> list:
        return []

    def changes_by_agent_run(self, *args: Any, **kwargs: Any) -> list:
        return []

    def contention_hotspots(self, *args: Any, **kwargs: Any) -> list:
        return []
