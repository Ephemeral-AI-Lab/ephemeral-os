"""PlanHealthMonitor — computes plan-health prefixes and posts checkpoint notes.

Extracted from Executor so task-execution logic stays separate from
observability of sibling task outcomes.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from team.models import Task
    from team.runtime.team_run import TeamRun

logger = logging.getLogger(__name__)

FAILURE_RATE_THRESHOLD = 0.4
MIN_STARTED_FOR_CRITICAL = 3
RETRY_WARNING_THRESHOLD = 3


class PlanHealthMonitor:
    """Emits plan-health warnings based on sibling statistics."""

    def __init__(self, team_run: "TeamRun") -> None:
        self.team_run = team_run

    async def compute_prefix(self, task: "Task") -> str | None:
        """Return a health prefix to prepend to an agent's prompt, or None."""
        if not task.parent_id:
            return None
        try:
            stats = await self.team_run.task_center.store.sibling_stats(task.parent_id)
        except Exception:
            return None
        lines: list[str] = []
        done = stats.get("done", 0)
        failed = stats.get("failed", 0)
        started = done + failed
        retry_total = stats.get("retry_total", 0)
        if (
            started >= MIN_STARTED_FOR_CRITICAL
            and started > 0
            and failed / started > FAILURE_RATE_THRESHOLD
        ):
            lines.append(
                f"**PLAN HEALTH CRITICAL:** {failed}/{started} sibling tasks "
                f"have failed. If your task depends on their output, stop and "
                f"note the dependency — the system will handle replanning."
            )
        if retry_total >= RETRY_WARNING_THRESHOLD:
            lines.append(
                f"**PLAN HEALTH WARNING:** {retry_total} retries across "
                f"sibling tasks. Check for systemic issues before proceeding."
            )
        return "\n".join(lines) if lines else None

    async def post_checkpoint_note(self, task: "Task", result: Any) -> str | None:
        """Post a checkpoint note summarizing the task outcome and plan health.

        Returns "replan" when sibling failure rate crosses the critical
        threshold, otherwise None.
        """
        tc = self.team_run.task_center
        try:
            stats = await tc.store.sibling_stats(task.parent_id)
        except Exception:
            logger.debug(
                "checkpoint note: sibling_stats failed for %s", task.id, exc_info=True
            )
            return None
        lines = [f"**Checkpoint: {task.id} ({task.agent_name}) → {task.status}**"]
        if task.failure_reason:
            lines.append(f"Failure: {task.failure_reason}")
        arbiter = getattr(self.team_run, "arbiter", None)
        if (
            arbiter is not None
            and getattr(arbiter, "initialized", False)
            and task.agent_run_id
        ):
            try:
                changes = arbiter.changes_by_agent_run(
                    self.team_run.id, task.agent_run_id
                )
                if changes:
                    lines.append(
                        f"Files touched: {', '.join(c.file_path for c in changes[:10])}"
                    )
            except Exception:
                pass
        action = None
        done, failed = stats.get("done", 0), stats.get("failed", 0)
        started = done + failed
        retry_total = stats.get("retry_total", 0)
        if (
            started >= MIN_STARTED_FOR_CRITICAL
            and started > 0
            and failed / started > FAILURE_RATE_THRESHOLD
        ):
            lines.append(
                f"PLAN HEALTH CRITICAL: {failed}/{started} sibling tasks failed"
            )
            action = "replan"
        elif retry_total >= RETRY_WARNING_THRESHOLD:
            lines.append(
                f"PLAN HEALTH WARNING: {retry_total} retries across sibling tasks"
            )
        note_owner = task.parent_id or task.id
        from team.models import Note

        try:
            await tc.notes.post(
                Note(
                    id=str(uuid.uuid4()),
                    task_id=note_owner,
                    agent_name="checkpoint",
                    content="\n".join(lines),
                    timestamp=time.time(),
                    paths=list(task.scope_paths) if task.scope_paths else [],
                )
            )
        except Exception:
            logger.debug(
                "checkpoint note: post failed for %s", task.id, exc_info=True
            )
        return action
