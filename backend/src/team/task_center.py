"""Task Center — append-only shared context log.

All notes are kept in-memory within the TaskCenter instance. Since all
executors in a TeamRun share the same TaskCenter, cross-executor visibility
is guaranteed without needing PostgreSQL persistence.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from team.models import Note

if TYPE_CHECKING:
    from team.models import Task

logger = logging.getLogger(__name__)


class TaskCenter:
    """Append-only shared context log (in-memory)."""

    def __init__(
        self,
        goal: str = "",
        user_request: str = "",
        team_run_id: str = "",
        **_kwargs: Any,
    ) -> None:
        self._notes: list[Note] = []
        self.goal = goal
        self.user_request = user_request
        self._team_run_id = team_run_id

    @staticmethod
    def _matches_scope(note_scopes: list[str], query_scopes: list[str]) -> bool:
        if not note_scopes:
            return True
        normalized_queries = [scope.rstrip("/") for scope in query_scopes]
        return any(
            note_scope.startswith(query_scope)
            for note_scope in note_scopes
            for query_scope in normalized_queries
        )

    async def post(self, note: Note) -> None:
        """Append a note."""
        self._notes.append(note)

    async def read(
        self,
        *,
        authors: list[str] | None = None,
        scope_paths: list[str] | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[Note]:
        """Read notes with optional filters."""
        results = list(self._notes)
        if authors:
            author_set = set(authors)
            results = [n for n in results if n.task_id in author_set]
        if scope_paths:
            results = [n for n in results if self._matches_scope(n.scope_paths, scope_paths)]
        if since is not None:
            results = [n for n in results if n.timestamp >= since]
        if limit is not None and limit > 0:
            results = results[-limit:]
        return results

    async def context_for(
        self,
        task: "Task",
        *,
        file_change_store: Any | None = None,
        task_lookup: Callable[[str], Awaitable["Task | None"]] | None = None,
        max_context_bytes: int = 200_000,
    ) -> str:
        """Build context string for a task. Fixed priority order:
        task (never trimmed) -> deps -> file changes -> parent chain."""
        budget = max_context_bytes
        sections: list[str] = []

        # Priority 1: The task itself (never trimmed)
        task_section = f"## Your task\n{task.task}"
        if task.scope_paths:
            task_section += f"\n\nScope: {', '.join(task.scope_paths)}"
        sections.append(task_section)
        budget -= len(task_section.encode())

        # Priority 2: Dep notes (direct deps only, not transitive)
        if task.deps and budget > 0:
            dep_notes = await self.read(authors=task.deps)
            if dep_notes:
                by_dep: dict[str, Note] = {}
                for n in dep_notes:
                    by_dep[n.task_id] = n
                deduped = list(by_dep.values())
                dep_section = self._render_notes("Context from dependencies", deduped)
                dep_bytes = len(dep_section.encode())
                if dep_bytes <= budget:
                    sections.append(dep_section)
                    budget -= dep_bytes
                else:
                    sections.append(
                        self._truncate_section("Context from dependencies", deduped, budget)
                    )
                    budget = 0

        # Priority 3: Recent file changes in scope (from FileChangeStore)
        if file_change_store is not None and getattr(file_change_store, "initialized", False) and budget > 0 and task.scope_paths:
            created_ts = task.created_at.timestamp() if task.created_at else 0.0
            changes = file_change_store.changes_since(created_ts)
            scoped = [
                e
                for e in changes
                if any(e.file_path.startswith(p.rstrip("/")) for p in task.scope_paths)
            ]
            if scoped:
                now = time.time()
                lines = [
                    f"- {e.file_path} ({e.edit_type} by {e.agent_id}, "
                    f"{int(now - e.created_at.timestamp())}s ago)"
                    for e in scoped
                ]
                change_section = "## Recent changes in your scope\n" + "\n".join(lines)
                change_bytes = len(change_section.encode())
                if change_bytes <= budget:
                    sections.append(change_section)
                    budget -= change_bytes

        # Priority 4: Parent chain (why this task exists)
        if task.parent_id and budget > 0:
            parent_ids = await self._parent_chain_ids(task, task_lookup=task_lookup)
            parent_notes = await self.read(authors=parent_ids)
            if parent_notes:
                parent_section = self._render_notes("Parent context", parent_notes)
                parent_bytes = len(parent_section.encode())
                if parent_bytes <= budget:
                    sections.append(parent_section)
                    budget -= parent_bytes
                else:
                    sections.append(self._truncate_section("Parent context", parent_notes, budget))

        return "\n\n".join(sections)

    def snapshot(self) -> list[Note]:
        return list(self._notes)

    def restore(self, notes: list[Note]) -> None:
        self._notes = list(notes)

    def _render_notes(self, header: str, notes: list[Note]) -> str:
        lines = [f"## {header}"]
        for n in notes:
            lines.append(f"### {n.agent_name} ({n.task_id})")
            lines.append(n.content)
        return "\n".join(lines)

    def _truncate_section(self, header: str, notes: list[Note], budget: int) -> str:
        sep = "\n"
        header_line = f"## {header}"
        remaining = budget - len(header_line.encode()) - len(sep.encode())
        lines = [header_line]
        for n in notes:
            entry = f"### {n.agent_name} ({n.task_id})\n{n.content}"
            entry_cost = len(entry.encode()) + len(sep.encode())
            if entry_cost <= remaining:
                lines.append(entry)
                remaining -= entry_cost
            else:
                safe_bytes = max(
                    0, remaining - len(sep.encode()) - len("\n...[truncated]".encode())
                )
                truncated = entry.encode()[:safe_bytes].decode("utf-8", errors="ignore")
                lines.append(truncated + "\n...[truncated]")
                break
        return sep.join(lines)

    async def _parent_chain_ids(
        self,
        task: "Task",
        *,
        task_lookup: Callable[[str], Awaitable["Task | None"]] | None,
    ) -> list[str]:
        if task.parent_id is None:
            return []
        if task_lookup is None:
            return [task.parent_id]
        parent_ids: list[str] = []
        seen: set[str] = set()
        current_id = task.parent_id
        while current_id and current_id not in seen:
            parent_ids.append(current_id)
            seen.add(current_id)
            parent = await task_lookup(current_id)
            current_id = parent.parent_id if parent is not None else None
        return parent_ids
