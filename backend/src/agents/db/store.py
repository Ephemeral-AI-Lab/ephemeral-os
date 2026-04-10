"""Agent definition persistence store."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any
from uuid import uuid4

from agents.db.model import AgentDefinitionRecord
from db.stores.definition_store import DefinitionStoreBase


class AgentDefinitionStore(DefinitionStoreBase[AgentDefinitionRecord]):
    """CRUD operations for agent definition records."""

    record_type = AgentDefinitionRecord

    def get_by_name(self, name: str, *, active_only: bool = True) -> AgentDefinitionRecord | None:
        return self._get_by_name(name, active_only=active_only)

    def list_active(
        self, *, tags: list[str] | None = None, limit: int = 50, offset: int = 0
    ) -> list[AgentDefinitionRecord]:
        with self._sf() as db:
            q = (
                db.query(AgentDefinitionRecord)
                .filter(AgentDefinitionRecord.is_active.is_(True))
                .order_by(AgentDefinitionRecord.name)
            )
            if tags:
                for tag in tags:
                    q = q.filter(AgentDefinitionRecord.tags.contains([tag]))
            return list(q.offset(offset).limit(limit).all())

    def update(self, name: str, updates: dict[str, Any]) -> AgentDefinitionRecord:
        return self._update_by_name(
            name,
            updates,
            active_only=False,
            missing_message=f"Agent definition '{name}' not found",
        )

    def soft_delete(self, name: str) -> bool:
        return self._soft_delete_by_name(name)

    def backfill_model_key(self, default_model_key: str) -> int:
        """Set model_key to *default_model_key* for all agents that have NULL or empty model."""
        with self._sf() as db:
            rows = (
                db.query(AgentDefinitionRecord)
                .filter(
                    (AgentDefinitionRecord.model.is_(None)) | (AgentDefinitionRecord.model == "")
                )
                .all()
            )
            for rec in rows:
                rec.model = default_model_key
                rec.updated_at = datetime.now(UTC)
            db.commit()
            return len(rows)

    def clone(self, source_name: str, new_name: str) -> AgentDefinitionRecord:
        with self._sf() as db:
            source = self._get_by_name_with_session(db, source_name)
            if source is None:
                raise KeyError(f"Source agent '{source_name}' not found")
            # Check if new_name already exists (including inactive)
            existing = self._get_by_name_with_session(db, new_name, active_only=False)
            if existing is not None:
                if existing.is_active:
                    raise KeyError(f"Agent '{new_name}' already exists")
                # Reactivate inactive record with cloned data
                self._apply_updates(existing, self._clone_payload(source))
                existing.is_active = True
                existing.version += 1
                existing.updated_at = datetime.now(UTC)
                db.commit()
                db.refresh(existing)
                return existing
            now = datetime.now(UTC)
            clone_record = AgentDefinitionRecord(
                id=str(uuid4()),
                name=new_name,
                **self._clone_payload(source),
                version=1,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(clone_record)
            db.commit()
            db.refresh(clone_record)
            return clone_record

    @staticmethod
    def _clone_payload(source: AgentDefinitionRecord) -> dict[str, Any]:
        return {
            "description": source.description,
            "system_prompt": source.system_prompt,
            "model": source.model,
            "effort": source.effort,
            "tool_call_limit": source.tool_call_limit,
            "toolkits": source.toolkits,
            "skills": source.skills or [],
            "hooks": source.hooks,
            "background": source.background,
            "initial_prompt": source.initial_prompt,
            "created_by": source.created_by,
            "tags": source.tags,
            "metadata_json": source.metadata_json,
        }
