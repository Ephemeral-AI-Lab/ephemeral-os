"""Skill definition persistence store."""

from __future__ import annotations

from typing import Any

from db.stores.definition_store import DefinitionStoreBase
from skills.db.model import SkillDefinitionRecord


class SkillDefinitionStore(DefinitionStoreBase[SkillDefinitionRecord]):
    """CRUD operations for skill definition records."""

    record_type = SkillDefinitionRecord

    def get_by_name(self, name: str) -> SkillDefinitionRecord | None:
        return self._get_by_name(name)

    def list_active(self, *, limit: int = 200, offset: int = 0) -> list[SkillDefinitionRecord]:
        return self._list_active(limit=limit, offset=offset, order_by=SkillDefinitionRecord.name)

    def update(self, name: str, updates: dict[str, Any]) -> SkillDefinitionRecord:
        return self._update_by_name(name, updates, missing_message=f"Skill '{name}' not found")

    def soft_delete(self, name: str) -> bool:
        return self._soft_delete_by_name(name)
