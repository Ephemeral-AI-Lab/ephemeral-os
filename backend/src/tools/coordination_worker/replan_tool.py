"""Request replan tool — worker agents signal issues back to coordinator."""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult

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


class RequestReplanInput(BaseModel):
    """Arguments for requesting a replan."""

    reason: str = Field(description="Brief summary of why replanning is needed")
    context: str = Field(description="Detailed context — error output, test results, failure analysis")
    suggestion: str = Field(default="", description="Optional hint for the replanner about what should happen next")


class RequestReplanTool(BaseTool):
    """Signal that a task encountered issues requiring replanning."""

    name = "request_replan"
    description = (
        "Signal that this task encountered issues requiring replanning. "
        "Persists a structured replan request and optionally triggers a replanner. "
        "You do NOT need to stop after calling this; finish your current work naturally."
    )
    input_model = RequestReplanInput

    def __init__(
        self,
        *,
        task_id: str = "",
        run_id: str = "",
        store: ArtifactStore | None = None,
        replan_handler: ReplanHandler | None = None,
        trigger_dispatch_fn: Callable[[], None] | None = None,
    ) -> None:
        self._task_id = task_id
        self._run_id = run_id
        self._store = store
        self._replan_handler = replan_handler
        self._trigger_dispatch_fn = trigger_dispatch_fn

    async def execute(
        self, arguments: RequestReplanInput, context: ToolExecutionContext
    ) -> ToolResult:
        replan_payload = {
            "type": "replan_request",
            "reason": arguments.reason,
            "context": arguments.context,
            "suggestion": arguments.suggestion,
            "task_id": self._task_id,
            "run_id": self._run_id,
        }

        # Persist as task artifact
        if self._store is not None:
            try:
                self._store.save_artifact(
                    self._run_id,
                    self._task_id,
                    artifact={"replan_request": replan_payload},
                )
            except Exception:
                logger.warning(
                    "Failed to persist replan_request artifact for task %s (run=%s)",
                    self._task_id,
                    self._run_id,
                    exc_info=True,
                )

        # Delegate replanning to handler
        replanner_spawned = False
        if self._replan_handler is not None:
            try:
                replanner_spawned = self._replan_handler.handle_replan(
                    self._task_id,
                    self._run_id,
                    arguments.reason,
                    arguments.context,
                    arguments.suggestion,
                )
            except Exception:
                logger.warning(
                    "Replan handler failed for task %s (run=%s)",
                    self._task_id,
                    self._run_id,
                    exc_info=True,
                )

        # Trigger dispatch so the replanner starts immediately
        if replanner_spawned and self._trigger_dispatch_fn is not None:
            try:
                self._trigger_dispatch_fn()
            except Exception:
                logger.debug(
                    "Failed to trigger dispatch after replan (run=%s)",
                    self._run_id,
                    exc_info=True,
                )

        return ToolResult(
            output="Replan request recorded."
            + (" Replanner task spawned." if replanner_spawned else "")
            + " Continue your current work — no need to stop.",
        )
