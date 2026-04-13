"""FileChangeStore — in-memory file-change tracking for team coordination.

Tracks file edits made by agents during a team run. All data is in-memory;
no PostgreSQL dependency. The Ledger handles hot-path reads (context_for,
same-run scope queries). This store handles:

  1. Cross-run contention history (query_edit_history for planner)
  2. Multi-process visibility (edits visible across executors in same process)
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Record dataclass (replaces ORM model)
# ---------------------------------------------------------------------------


@dataclass
class FileChangeRecord:
    """Record of a file edit by an agent."""

    file_path: str
    agent_id: str
    team_run_id: str = ""
    agent_run_id: str = ""
    edit_type: str = "edit"
    old_hash: str = ""
    new_hash: str = ""
    description: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    id: int = 0

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
# In-memory Store
# ---------------------------------------------------------------------------


class FileChangeStore:
    """In-memory file-change tracking. No PostgreSQL dependency."""

    initialized: bool = True

    def __init__(self) -> None:
        self._records: list[FileChangeRecord] = []
        self._next_id = 1

    def initialize(self, *args: Any, **kwargs: Any) -> None:
        """No-op — kept for backwards compatibility with SyncStoreMixin callers."""
        self.initialized = True

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
        rec = FileChangeRecord(
            id=self._next_id,
            team_run_id=team_run_id,
            file_path=file_path,
            agent_id=agent_id,
            agent_run_id=agent_run_id,
            edit_type=edit_type,
            old_hash=old_hash,
            new_hash=new_hash,
            description=description,
        )
        self._next_id += 1
        self._records.append(rec)
        return rec

    def changes_in_scope(
        self,
        team_run_id: str,
        scope_prefixes: list[str],
        since: float,
    ) -> list[FileChangeRecord]:
        """Return file changes under scope prefixes since a timestamp."""
        if not scope_prefixes:
            return []
        cutoff = datetime.fromtimestamp(since, tz=timezone.utc)
        normalized = [p.rstrip("/") for p in scope_prefixes]
        return [
            r for r in self._records
            if r.team_run_id == team_run_id
            and r.created_at > cutoff
            and any(r.file_path.startswith(prefix) for prefix in normalized)
        ]

    def external_changes_in_scope(
        self,
        team_run_id: str,
        scope_prefixes: list[str],
        since: float,
        exclude_run_id: str | None = None,
    ) -> list[FileChangeRecord]:
        """Return changes in scope NOT made by a specific agent run."""
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
        cutoff = datetime.fromtimestamp(since, tz=timezone.utc)
        results = [r for r in self._records if r.created_at > cutoff]
        if team_run_id is not None:
            results = [r for r in results if r.team_run_id == team_run_id]
        return results

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
        records = self._records
        if team_run_id is not None:
            records = [r for r in records if r.team_run_id == team_run_id]
        counter: Counter[str] = Counter(r.file_path for r in records)
        return counter.most_common(limit)

    def who_changed(
        self,
        file_path: str,
        team_run_id: str | None = None,
    ) -> list[FileChangeRecord]:
        """Return all edit records for a specific file."""
        results = [r for r in self._records if r.file_path == file_path]
        if team_run_id is not None:
            results = [r for r in results if r.team_run_id == team_run_id]
        return results

    def changes_by_agent_run(
        self,
        team_run_id: str,
        agent_run_id: str,
    ) -> list[FileChangeRecord]:
        """Return all file changes made by a specific agent run."""
        if not agent_run_id:
            return []
        return [
            r for r in self._records
            if r.team_run_id == team_run_id and r.agent_run_id == agent_run_id
        ]

    def contention_hotspots(
        self,
        scope_prefixes: list[str],
        limit: int = 10,
        days: int = 7,
    ) -> list[ContentionHotspot]:
        """Cross-run contention hotspots: files edited by many agents."""
        if not scope_prefixes:
            return []
        from datetime import timedelta
        cutoff = _utcnow() - timedelta(days=days)
        normalized = [p.rstrip("/") for p in scope_prefixes]
        scoped = [
            r for r in self._records
            if r.created_at > cutoff
            and any(r.file_path.startswith(prefix) for prefix in normalized)
        ]
        # Group by file_path
        by_file: dict[str, set[str]] = {}
        counts: Counter[str] = Counter()
        for r in scoped:
            by_file.setdefault(r.file_path, set()).add(r.agent_id)
            counts[r.file_path] += 1
        results = [
            ContentionHotspot(
                file_path=fp,
                agent_count=len(agents),
                edit_count=counts[fp],
            )
            for fp, agents in by_file.items()
            if len(agents) > 1
        ]
        results.sort(key=lambda h: (-h.agent_count, -h.edit_count))
        return results[:limit]


# ---------------------------------------------------------------------------
# Null fallback (no-op for tests)
# ---------------------------------------------------------------------------


class NullFileChangeStore:
    """No-op store for tests that don't need file change tracking."""

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
