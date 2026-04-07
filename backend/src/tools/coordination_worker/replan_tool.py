"""Request replan tool — worker agents signal issues back to coordinator."""

from __future__ import annotations

import logging
from typing import Callable, Protocol, runtime_checkable

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult
from tools.core.decorator import tool

logger = logging.getLogger(__name__)


@runtime_checkable
class ArtifactStore(Protocol):
    """Minimal interface for persisting replan artifacts."""

    def save_artifact(self, run_id: str, task_id: str, *, artifact: dict) -> None: ...


@runtime_checkable
class ReplanHandler(Protocol):
    """Handles replan requests — spawns replanner tasks, updates plan state."""

    def handle_replan(
        self,
        task_id: str,
        run_id: str,
        reason: str,
        context: str,
        suggestion: str,
    ) -> bool: ...


def make_request_replan_tool(
    *,
    task_id: str = "",
    run_id: str = "",
    store: ArtifactStore | None = None,
    replan_handler: ReplanHandler | None = None,
    trigger_dispatch_fn: Callable[[], None] | None = None,
) -> BaseTool:
    """Create a replan request tool with pre-bound coordination state."""

    @tool(
        name="request_replan",
        description=(
            "Signal that this task encountered issues requiring replanning. "
            "Persists a structured replan request and optionally triggers a replanner. "
            "You do NOT need to stop after calling this; finish your current work naturally."
        ),
    )
    async def request_replan(
        reason: str,
        context_detail: str,
        suggestion: str = "",
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Signal that a task encountered issues requiring replanning.

        Args:
            reason: Brief summary of why replanning is needed
            context_detail: Detailed context — error output, test results, failure analysis
            suggestion: Optional hint for the replanner about what should happen next
        """
        replan_payload = {
            "type": "replan_request",
            "reason": reason,
            "context": context_detail,
            "suggestion": suggestion,
            "task_id": task_id,
            "run_id": run_id,
        }

        # Persist as task artifact
        if store is not None:
            try:
                store.save_artifact(
                    run_id,
                    task_id,
                    artifact={"replan_request": replan_payload},
                )
            except Exception:
                logger.warning(
                    "Failed to persist replan_request artifact for task %s (run=%s)",
                    task_id,
                    run_id,
                    exc_info=True,
                )

        # Delegate replanning to handler
        replanner_spawned = False
        if replan_handler is not None:
            try:
                replanner_spawned = replan_handler.handle_replan(
                    task_id,
                    run_id,
                    reason,
                    context_detail,
                    suggestion,
                )
            except Exception:
                logger.warning(
                    "Replan handler failed for task %s (run=%s)",
                    task_id,
                    run_id,
                    exc_info=True,
                )

        # Trigger dispatch so the replanner starts immediately
        if replanner_spawned and trigger_dispatch_fn is not None:
            try:
                trigger_dispatch_fn()
            except Exception:
                logger.debug(
                    "Failed to trigger dispatch after replan (run=%s)",
                    run_id,
                    exc_info=True,
                )

        return ToolResult(
            output="Replan request recorded."
            + (" Replanner task spawned." if replanner_spawned else "")
            + " Continue your current work — no need to stop.",
        )

    return request_replan
