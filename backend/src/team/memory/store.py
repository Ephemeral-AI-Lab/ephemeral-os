"""SQL-backed store for typed durable team memory."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from team.memory.model import TeamMemoryRecordModel


@dataclass
class TeamMemoryRecord:
    """One typed durable team memory record."""

    kind: str
    content: dict[str, Any]
    memory_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_key: str = ""
    repo_root: str = ""
    status: str = "active"
    scope: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    observed_at: float = field(default_factory=time.time)
    stale_hint: str = ""
    superseded_by: str = ""


class TeamMemoryStore:
    """CRUD for durable typed team memories."""

    def __init__(self) -> None:
        self._session_factory: sessionmaker[Session] | None = None

    def initialize(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def is_initialised(self) -> bool:
        return self._session_factory is not None

    @property
    def _sf(self) -> sessionmaker[Session]:
        assert self._session_factory is not None, "TeamMemoryStore not initialised"
        return self._session_factory

    def append(self, record: TeamMemoryRecord) -> bool:
        return self.append_many([record]) == 1

    def append_many(self, records: Iterable[TeamMemoryRecord]) -> int:
        items = [record for record in records if record.kind]
        if not items:
            return 0
        with self._sf() as db:
            for record in items:
                db.add(
                    TeamMemoryRecordModel(
                        memory_id=record.memory_id,
                        project_key=record.project_key,
                        repo_root=record.repo_root,
                        kind=record.kind,
                        status=record.status,
                        scope_json=dict(record.scope),
                        content_json=dict(record.content),
                        source_json=dict(record.source),
                        observed_at=float(record.observed_at or 0.0),
                        stale_hint=record.stale_hint,
                        superseded_by=record.superseded_by,
                    )
                )
            db.commit()
        return len(items)

    def query(
        self,
        *,
        project_key: str,
        kinds: Iterable[str] | None = None,
        scope_paths: Iterable[str] | None = None,
        include_stale: bool = False,
        limit: int = 50,
    ) -> list[TeamMemoryRecord]:
        if not project_key:
            return []
        requested_kinds = [kind for kind in (kinds or []) if kind]
        requested_paths = [path for path in (scope_paths or []) if path]
        with self._sf() as db:
            stmt = (
                select(TeamMemoryRecordModel)
                .where(TeamMemoryRecordModel.project_key == project_key)
                .order_by(TeamMemoryRecordModel.observed_at.desc())
            )
            if requested_kinds:
                stmt = stmt.where(TeamMemoryRecordModel.kind.in_(requested_kinds))
            if not include_stale:
                stmt = stmt.where(TeamMemoryRecordModel.status != "stale")
            if not requested_paths:
                stmt = stmt.limit(max(1, int(limit)))
            rows = db.execute(stmt).scalars().all()
        results = [self._to_record(row) for row in rows]
        if not requested_paths:
            return results
        filtered: list[TeamMemoryRecord] = []
        for record in results:
            paths = record.scope.get("paths")
            if not isinstance(paths, list):
                continue
            if any(str(path) in requested_paths for path in paths):
                filtered.append(record)
                if len(filtered) >= max(1, int(limit)):
                    break
        return filtered

    @staticmethod
    def _to_record(row: TeamMemoryRecordModel) -> TeamMemoryRecord:
        return TeamMemoryRecord(
            memory_id=row.memory_id,
            project_key=row.project_key,
            repo_root=row.repo_root,
            kind=row.kind,
            status=row.status,
            scope=dict(row.scope_json or {}),
            content=dict(row.content_json or {}),
            source=dict(row.source_json or {}),
            observed_at=float(row.observed_at or 0.0),
            stale_hint=row.stale_hint or "",
            superseded_by=row.superseded_by or "",
        )


_default_store = TeamMemoryStore()


def get_default_store() -> TeamMemoryStore:
    """Return the process-wide default team memory store."""
    return _default_store
