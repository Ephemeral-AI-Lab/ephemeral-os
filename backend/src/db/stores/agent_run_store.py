"""Agent run persistence store."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from db.models.agent_run import AgentRunRecord
from db.stores.base import SyncStoreMixin


class AgentRunStore(SyncStoreMixin):
    """CRUD operations for agent run records."""

    # -- run CRUD --------------------------------------------------------------

    def create_run(
        self,
        *,
        agent_run_id: str,
        task_id: str,
        agent_name: str,
        initial_messages: list[dict[str, Any]] | None = None,
    ) -> AgentRunRecord:
        """Create a new agent run record for one persisted task."""
        with self._sf() as db:
            record = AgentRunRecord(
                id=agent_run_id,
                task_id=task_id,
                agent_name=agent_name,
                initial_messages=initial_messages,
                created_at=datetime.now(UTC),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record

    def finish_run(
        self,
        agent_run_id: str,
        *,
        message_history: list[dict[str, Any]] | None = None,
        terminal_tool_result: dict[str, Any] | None = None,
        token_count: int = 0,
        error: str | None = None,
    ) -> AgentRunRecord | None:
        with self._sf() as db:
            record = db.get(AgentRunRecord, agent_run_id)
            if record is None:
                return None
            record.message_history = message_history
            record.terminal_tool_result = terminal_tool_result
            record.token_count = token_count
            record.error = error
            record.finished_at = datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return record

    def get_run(self, agent_run_id: str) -> AgentRunRecord | None:
        with self._sf() as db:
            return db.get(AgentRunRecord, agent_run_id)
