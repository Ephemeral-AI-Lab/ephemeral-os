"""ScopeChangeNotifier — warns tasks when their scope files are edited externally.

Extracted from Executor so scope-drift detection is a separate concern
from task execution.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from team.models import Task
    from team.runtime.team_run import TeamRun

logger = logging.getLogger(__name__)


class ScopeChangeNotifier:
    """Detects external edits to a task's scope_paths and posts warnings."""

    def __init__(self, team_run: "TeamRun") -> None:
        self.team_run = team_run

    async def inject_warning(self, task: "Task") -> None:
        if not task.scope_paths:
            return
        store = getattr(self.team_run, "file_change_store", None)
        if store is None or not getattr(store, "initialized", False):
            return
        created_ts = task.created_at.timestamp() if task.created_at else 0.0
        changes = store.changes_since(created_ts)
        own_run = task.agent_run_id or ""
        external = [
            e
            for e in changes
            if e.agent_run_id != own_run
            and any(e.file_path.startswith(p.rstrip("/")) for p in task.scope_paths)
        ]
        if not external:
            return
        now = time.time()
        lines = [
            "## Warning: scope changes detected since plan creation",
            "The following files in your scope were modified externally:",
        ]
        for e in external:
            lines.append(
                f"- {e.file_path} ({e.edit_type} by {e.agent_id}, "
                f"{int(now - e.created_at.timestamp())}s ago)"
            )
        lines.append(
            "Review these changes before proceeding. "
            "Stop and note the issue if your task is no longer valid — the system will handle replanning."
        )
        from team.models import Note

        try:
            await self.team_run.task_center.notes.post(
                Note(
                    id=str(uuid.uuid4()),
                    task_id=task.id,
                    agent_name="system",
                    content="\n".join(lines),
                    timestamp=now,
                    paths=list(task.scope_paths),
                )
            )
        except Exception:
            logger.debug(
                "Failed to persist scope warning for %s", task.id, exc_info=True
            )
