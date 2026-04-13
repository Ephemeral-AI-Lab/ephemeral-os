"""Shared helpers for small definition-style SQLAlchemy stores."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from sqlalchemy.orm import Session, sessionmaker

from db.stores.base import SyncStoreMixin

RecordT = TypeVar("RecordT")


class DefinitionStoreBase(SyncStoreMixin, Generic[RecordT]):
    """Provide common CRUD primitives for name-keyed definition records."""

    record_type: type[RecordT]
    immutable_fields: tuple[str, ...] = ("id", "name", "created_at", "version")

    def create(self, record: RecordT) -> RecordT:
        with self._sf() as db:
            db.add(record)
            db.commit()
            db.refresh(record)
            return record

    def _get_by_name(self, name: str, *, active_only: bool = True) -> RecordT | None:
        with self._sf() as db:
            return self._get_by_name_with_session(db, name, active_only=active_only)

    def _list_active(self, *, limit: int, offset: int, order_by) -> list[RecordT]:
        with self._sf() as db:
            return list(
                db.query(self.record_type)
                .filter(self.record_type.is_active.is_(True))
                .order_by(order_by)
                .offset(offset)
                .limit(limit)
                .all()
            )

    def _apply_updates(self, record: RecordT, updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if hasattr(record, key) and key not in self.immutable_fields:
                setattr(record, key, value)

    def _update_by_name(
        self,
        name: str,
        updates: dict[str, Any],
        *,
        active_only: bool = True,
        missing_message: str,
    ) -> RecordT:
        with self._sf() as db:
            record = self._get_by_name_with_session(db, name, active_only=active_only)
            if record is None:
                raise KeyError(missing_message)
            self._apply_updates(record, updates)
            record.version += 1
            record.updated_at = datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return record

    def _get_by_name_with_session(
        self, db: Session, name: str, *, active_only: bool = True
    ) -> RecordT | None:
        query = db.query(self.record_type).filter(self.record_type.name == name)
        if active_only:
            query = query.filter(self.record_type.is_active.is_(True))
        return query.first()

    def _soft_delete_by_name(self, name: str) -> bool:
        with self._sf() as db:
            record = self._get_by_name_with_session(db, name, active_only=True)
            if record is None:
                return False
            record.is_active = False
            record.updated_at = datetime.now(UTC)
            db.commit()
            return True
