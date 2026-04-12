"""Task Center — append-only shared context log.

Replaces ProjectContext, InMemoryArtifactStore, and 3-tier briefing system.

When a NoteStore (PG-backed) is attached, notes are written through to
PostgreSQL for cross-process visibility and crash recovery. The in-memory
list remains the hot-path for reads within the same process.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from typing import TYPE_CHECKING, Any

from team.models import Note

if TYPE_CHECKING:
    from code_intelligence.editing.arbiter import Arbiter
    from team.models import Task

logger = logging.getLogger(__name__)


class TaskCenter:
    """Append-only shared context log with optional PG write-through."""

    def __init__(
        self,
        goal: str = "",
        user_request: str = "",
        note_store: Any = None,
        team_run_id: str = "",
    ) -> None:
        self._notes: list[Note] = []
        self.goal = goal
        self.user_request = user_request
        self._note_store = note_store  # NoteStore | NullNoteStore | None
        self._team_run_id = team_run_id

    def post(self, note: Note) -> None:
        """Append a note. list.append() is GIL-atomic in CPython.

        If a PG-backed NoteStore is attached, the note is also flushed
        asynchronously via fire-and-forget task on the running event loop.
        """
        self._notes.append(note)
        if self._note_store is not None and getattr(self._note_store, "initialized", False):
            self._schedule_pg_flush(note)

    def _schedule_pg_flush(self, note: Note) -> None:
        """Fire-and-forget flush of a single note to PG."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop — skip PG flush (sync context)
        loop.create_task(self._flush_note(note))

    async def _flush_note(self, note: Note) -> None:
        """Convert Note dataclass to TaskNoteRecord and insert."""
        try:
            from team.persistence.task_note_record import TaskNoteRecord
            record = TaskNoteRecord(
                id=_uuid.UUID(note.id) if note.id else _uuid.uuid4(),
                team_run_id=self._team_run_id,
                task_id=note.task_id,
                agent_name=note.agent_name,
                content=note.content,
                scope_ltree=list(note.scope_paths) if note.scope_paths else [],
            )
            await self._note_store.insert(record)
        except Exception:
            logger.debug("Failed to flush note %s to PG", note.id, exc_info=True)

    def read(self, *, authors: list[str] | None = None,
             scope_paths: list[str] | None = None,
             since: float | None = None,
             limit: int | None = None) -> list[Note]:
        """Filter notes. Snapshot-based — safe under concurrent asyncio tasks.
        scope_paths uses prefix matching (same semantics as ltree <@)."""
        results = list(self._notes)  # snapshot: isolate from concurrent post/restore
        if authors:
            author_set = set(authors)
            results = [n for n in results if n.task_id in author_set]
        if scope_paths:
            results = [n for n in results
                       if n.scope_paths and any(
                           note_scope.startswith(query_scope.rstrip('/'))
                           for note_scope in n.scope_paths
                           for query_scope in scope_paths)]
        if since is not None:
            results = [n for n in results if n.timestamp >= since]
        if limit is not None and limit > 0:
            results = results[-limit:]
        return results

    def context_for(
        self,
        task: "Task",
        *,
        arbiter: "Arbiter | None" = None,
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
            dep_notes = self.read(authors=task.deps)
            if dep_notes:
                dep_section = self._render_notes("Context from dependencies", dep_notes)
                dep_bytes = len(dep_section.encode())
                if dep_bytes <= budget:
                    sections.append(dep_section)
                    budget -= dep_bytes
                else:
                    sections.append(self._truncate_section(
                        "Context from dependencies", dep_notes, budget))
                    budget = 0

        # Priority 3: Recent file changes in scope (ground truth from Arbiter)
        if arbiter is not None and budget > 0 and task.scope_paths:
            created_ts = task.created_at.timestamp() if task.created_at else 0.0
            changes = arbiter.changes_since(created_ts)
            scoped = [
                e for e in changes
                if any(
                    e.file_path.startswith(p.rstrip("/"))
                    for p in task.scope_paths
                )
            ]
            if scoped:
                now = time.time()
                lines = [
                    f"- {e.file_path} ({e.edit_type} by {e.agent_id}, "
                    f"{int(now - e.timestamp)}s ago)"
                    for e in scoped
                ]
                change_section = "## Recent changes in your scope\n" + "\n".join(lines)
                change_bytes = len(change_section.encode())
                if change_bytes <= budget:
                    sections.append(change_section)
                    budget -= change_bytes

        # Priority 4: Parent chain (why this task exists)
        if task.parent_id and budget > 0:
            parent_notes = self.read(authors=[task.parent_id])
            if parent_notes:
                parent_section = self._render_notes("Parent context", parent_notes)
                parent_bytes = len(parent_section.encode())
                if parent_bytes <= budget:
                    sections.append(parent_section)
                    budget -= parent_bytes
                else:
                    sections.append(self._truncate_section(
                        "Parent context", parent_notes, budget))

        return "\n\n".join(sections)

    def hydrate(self, notes: list[Note]) -> None:
        """Hydrate in-memory state from an external store (e.g. PostgreSQL).

        Required for multi-process deployments where notes written by other
        executor processes are not visible in this process's _notes list.
        Single-process deployments can skip this — _notes is already complete.

        Deduplicates by note ID to avoid double-counting notes that exist
        in both the local _notes list and the external store.
        """
        seen = {n.id for n in self._notes}
        for n in notes:
            if n.id not in seen:
                self._notes.append(n)
                seen.add(n.id)

    async def hydrate_from_pg(self, task_ids: list[str]) -> None:
        """Hydrate notes from PG for specific task IDs (dependency context).

        Called before context_for() when cross-process notes may exist.
        """
        if self._note_store is None or not getattr(self._note_store, "initialized", False):
            return
        if not task_ids:
            return
        try:
            records = await self._note_store.query_by_task_ids(
                self._team_run_id, task_ids,
            )
            pg_notes = [
                Note(
                    id=str(rec.id),
                    task_id=rec.task_id,
                    agent_name=rec.agent_name,
                    content=rec.content,
                    scope_paths=list(rec.scope_ltree) if rec.scope_ltree else [],
                )
                for rec in records
            ]
            self.hydrate(pg_notes)
        except Exception:
            logger.debug("Failed to hydrate notes from PG", exc_info=True)

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
            # Account for the separator that join() will insert before this entry
            entry_cost = len(entry.encode()) + len(sep.encode())
            if entry_cost <= remaining:
                lines.append(entry)
                remaining -= entry_cost
            else:
                safe_bytes = max(0, remaining - len(sep.encode()) - len("\n...[truncated]".encode()))
                truncated = entry.encode()[:safe_bytes].decode("utf-8", errors="ignore")
                lines.append(truncated + "\n...[truncated]")
                break
        return sep.join(lines)
