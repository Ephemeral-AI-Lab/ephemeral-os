"""NoteManager — in-memory note lifecycle management.

Extracted from TaskCenter. Owns the in-memory note store, posting,
reading, context building, and scope filtering. Persistence of note events
is delegated to the event store callback.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from team.models import Note, Task

if TYPE_CHECKING:
    from team.persistence.events import TeamRunEvent

logger = logging.getLogger("team.task_center")


def _note_preview(content: str, *, limit: int = 240) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


class NoteManager:
    """In-memory note lifecycle management.

    Owns the note store, scope filtering, context building.
    Emits events via an event_store callback.
    """

    def __init__(
        self,
        team_run_id: str,
        event_store_cb: Callable[[TeamRunEvent], None] | None = None,
        get_task_fn: Callable[[str], Any] | None = None,
        task_store: Any = None,
        file_change_store: Any = None,
    ) -> None:
        self._notes: list[Note] = []
        self._team_run_id = team_run_id
        self._event_store_cb = event_store_cb
        self._get_task_fn = get_task_fn
        self._task_store = task_store
        self._file_change_store = file_change_store
        self._blocker_provider: Callable[[], Awaitable[list[Any]]] | None = None

    def set_blocker_provider(
        self, provider: Callable[[], Awaitable[list[Any]]] | None
    ) -> None:
        """Register an async callable that returns current active blockers.

        When set, ``context_for`` will fetch active blockers automatically
        (caller may also pass ``active_blockers`` explicitly to override).
        """
        self._blocker_provider = provider

    @staticmethod
    def _matches_scope(note_scopes: list[str], query_scopes: list[str]) -> bool:
        if not note_scopes:
            return True
        normalized = [s.rstrip("/") for s in query_scopes if s]
        return any(NoteManager._scope_overlaps(ns, qs) for ns in note_scopes for qs in normalized)

    @staticmethod
    def _scope_overlaps(note_scope: str, query_scope: str) -> bool:
        n, q = note_scope.rstrip("/"), query_scope.rstrip("/")
        if not n or not q:
            return False
        return n == q or n.startswith(q + "/") or q.startswith(n + "/")

    @staticmethod
    def _render_notes(header: str, notes: list[Note]) -> str:
        lines = [f"## {header}"]
        for n in notes:
            lines.append(f"### {n.agent_name} ({n.task_id})")
            lines.append(n.content)
        return "\n".join(lines)

    @staticmethod
    def _latest_notes_per_task(notes: list[Note]) -> list[Note]:
        latest: dict[str, Note] = {}
        for note in notes:
            latest[note.task_id] = note
        return list(latest.values())

    @staticmethod
    def _truncate_section(header: str, notes: list[Note], budget: int) -> str:
        sep = "\n"
        header_line = f"## {header}"
        remaining = budget - len(header_line.encode()) - len(sep.encode())
        lines = [header_line]
        for n in notes:
            entry = f"### {n.agent_name} ({n.task_id})\n{n.content}"
            cost = len(entry.encode()) + len(sep.encode())
            if cost <= remaining:
                lines.append(entry)
                remaining -= cost
            else:
                safe = max(0, remaining - len(sep.encode()) - len("\n...[truncated]".encode()))
                lines.append(
                    entry.encode()[:safe].decode("utf-8", errors="ignore") + "\n...[truncated]"
                )
                break
        return sep.join(lines)

    async def _parent_chain_ids(self, task: Task) -> list[str]:
        """Walk up the parent chain collecting all ancestor task IDs."""
        if task.parent_id is None:
            return []
        parent_ids: list[str] = []
        seen: set[str] = set()
        current_id = task.parent_id
        while current_id and current_id not in seen:
            parent_ids.append(current_id)
            seen.add(current_id)
            parent = await self.get_task(current_id)
            current_id = parent.parent_id if parent is not None else None
        return parent_ids

    async def get_task(self, task_id: str) -> Task | None:
        if self._get_task_fn is not None:
            return await self._get_task_fn(task_id)
        if self._task_store is None:
            return None
        rec = await self._task_store.get_record(task_id)
        if rec is None:
            return None
        from team.persistence.task_store import record_to_task

        return record_to_task(rec)

    def snapshot(self) -> list[Note]:
        """Return a copy of all notes (for checkpointing)."""
        return list(self._notes)

    def restore(self, notes: list[Note]) -> None:
        """Restore notes from a snapshot (for resume)."""
        self._notes = list(notes)

    async def post(self, note: Note) -> None:
        """Append a note and emit the posted event."""
        self._notes.append(note)
        auto_generated = note.agent_name.endswith(" (auto)")
        preview = _note_preview(note.content)
        logger.info(
            "[task_center] %snote task=%s agent=%s scope=%s preview=%s",
            "auto-" if auto_generated else "",
            note.task_id,
            note.agent_name,
            ",".join(note.scope_paths) if note.scope_paths else "-",
            preview,
        )
        if self._event_store_cb is not None:
            from team.persistence.events import make_note_posted

            self._event_store_cb(
                make_note_posted(
                    self._team_run_id,
                    task_id=note.task_id,
                    agent_name=note.agent_name,
                    auto=auto_generated,
                    scope_paths=note.scope_paths,
                    content_preview=preview,
                    content_bytes=len(note.content.encode("utf-8")),
                )
            )

    async def read(
        self,
        *,
        authors: list[str] | None = None,
        scope_paths: list[str] | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[Note]:
        """Filter and return notes by author, scope, timestamp, and limit."""
        results = list(self._notes)
        if authors:
            s = set(authors)
            results = [n for n in results if n.task_id in s]
        if scope_paths:
            results = [n for n in results if self._matches_scope(n.scope_paths, scope_paths)]
        if since is not None:
            results = [n for n in results if n.timestamp >= since]
        if limit is not None and limit > 0:
            results = results[-limit:]
        return results

    async def read_notes(
        self,
        *,
        task_id: str,
        scope: str = "full",
        keyword: str | None = None,
        scope_paths: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Note]:
        """Read notes with scope filtering.

        Scopes:
            full — entire note store
            siblings — sibling tasks and their children
        """
        if scope == "full":
            notes = await self.read(scope_paths=scope_paths, limit=limit)
        elif scope == "siblings":
            task = await self.get_task(task_id)
            if task is None:
                return []
            sibling_ids = await self._sibling_subtree_ids(task.parent_id)
            sibling_ids = [tid for tid in sibling_ids if tid != task_id]
            notes = await self.read(
                authors=sibling_ids,
                scope_paths=scope_paths,
                limit=limit,
            )
        else:
            notes = await self.read(scope_paths=scope_paths, limit=limit)
        if keyword:
            kw = keyword.lower()
            notes = [n for n in notes if kw in n.content.lower()]
        return notes

    async def _sibling_subtree_ids(self, parent_id: str | None) -> list[str]:
        """Get all task IDs in the sibling subtree under a parent."""
        if self._task_store is None:
            return []
        return await self._task_store.sibling_subtree_ids(parent_id)

    async def read_sibling_notes(
        self,
        parent_id: str,
        *,
        keyword: str | None = None,
        scope_paths: list[str] | None = None,
    ) -> str:
        """Read notes from sibling tasks under the same parent."""
        sibling_ids = await self._sibling_subtree_ids(parent_id)
        if not sibling_ids:
            return ""
        notes = await self.read(
            authors=sibling_ids,
            scope_paths=scope_paths,
        )
        if keyword:
            kw = keyword.lower()
            notes = [n for n in notes if kw in n.content.lower()]
        if not notes:
            return ""
        return self._render_notes("Sibling notes", notes)

    @staticmethod
    def _render_active_blockers(active_blockers: list[Any]) -> str:
        lines = ["## Active Blockers (in-progress)"]
        for b in active_blockers:
            status = getattr(b.status, "value", b.status)
            paths = list(getattr(b, "root_cause_paths", []) or [])
            reason = getattr(b, "reason", "") or ""
            fix_task_id = getattr(b, "fix_task_id", None)
            lines.append(f"### {b.id} ({status})")
            lines.append(f"- root_cause_paths: {paths}")
            lines.append(f"- reason: {reason}")
            if fix_task_id:
                lines.append(f"- fix_task_id: {fix_task_id}")
        return "\n".join(lines)

    async def context_for(
        self,
        task: Task,
        *,
        max_context_bytes: int = 200_000,
        file_change_store: Any = None,
        active_blockers: list[Any] | None = None,
    ) -> str:
        if file_change_store is None:
            file_change_store = self._file_change_store
        """Build context string for a task. No external callbacks needed.

        ``active_blockers`` — optional list of in-progress Blocker records. When
        present, a high-priority section is rendered so the replanner can see
        existing ASSESSING/FIXING blockers before deciding whether to call
        ``declare_blocker`` (dedup is skill-driven, not mechanical).
        """
        if active_blockers is None and self._blocker_provider is not None:
            try:
                active_blockers = await self._blocker_provider()
            except Exception:
                logger.exception("blocker_provider failed; continuing without blockers")
                active_blockers = []

        budget = max_context_bytes
        sections: list[str] = []

        if task.retry_count and task.retry_count > 0:
            s = (
                f"## ⚠ RETRY #{task.retry_count} of {task.max_retries}\n"
                f"Your previous attempt at this task failed. "
                f"Do NOT repeat the same approach — read the retry notes below "
                f"for what went wrong."
            )
            if task.retry_count >= task.max_retries:
                s += (
                    f"\n\n**This is your LAST attempt.** If you cannot fix the "
                    f"issue with a different approach, call `request_replan()` "
                    f"with a clear diagnostic so the replanner can restructure the work."
                )
            sections.append(s)
            budget -= len(s.encode())

        task_section = f"## Your task\n{task.task}"
        if task.scope_paths:
            task_section += f"\n\nScope: {', '.join(task.scope_paths)}"
        sections.append(task_section)
        budget -= len(task_section.encode())

        if active_blockers and budget > 0:
            sec = self._render_active_blockers(active_blockers)
            b = len(sec.encode())
            if b <= budget:
                sections.append(sec)
                budget -= b

        if task.retry_count and task.retry_count > 0 and budget > 0:
            self_notes = await self.read(authors=[task.id])
            if self_notes:
                sec = self._render_notes("Previous attempt context", self_notes)
                b = len(sec.encode())
                if b <= budget:
                    sections.append(sec)
                    budget -= b
                else:
                    sections.append(
                        self._truncate_section("Previous attempt context", self_notes, budget)
                    )
                    budget = 0

        if task.deps and budget > 0:
            dep_notes = await self.read(authors=task.deps)
            if dep_notes:
                deduped = self._latest_notes_per_task(dep_notes)
                sec = self._render_notes("Context from dependencies", deduped)
                b = len(sec.encode())
                if b <= budget:
                    sections.append(sec)
                    budget -= b
                else:
                    sections.append(
                        self._truncate_section("Context from dependencies", deduped, budget)
                    )
                    budget = 0

        fcs = file_change_store
        if (
            fcs is not None
            and getattr(fcs, "initialized", False)
            and budget > 0
            and task.scope_paths
        ):
            created_ts = task.created_at.timestamp() if task.created_at else 0.0
            changes = fcs.changes_since(created_ts)
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
                sec = "## Recent changes in your scope\n" + "\n".join(lines)
                b = len(sec.encode())
                if b <= budget:
                    sections.append(sec)
                    budget -= b

        if task.parent_id and budget > 0:
            parent_ids = await self._parent_chain_ids(task)
            parent_notes = await self.read(authors=parent_ids)
            if parent_notes:
                deduped = self._latest_notes_per_task(parent_notes)
                sec = self._render_notes("Parent context", deduped)
                b = len(sec.encode())
                if b <= budget:
                    sections.append(sec)
                    budget -= b
                else:
                    sections.append(self._truncate_section("Parent context", deduped, budget))

        return "\n\n".join(sections)
