"""Request replan tool — worker agents signal issues back to coordinator."""

from __future__ import annotations

import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


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
        "Persists a structured replan summary, spawns a replanner task, and "
        "triggers dispatch so the replanner starts in parallel. "
        "You do NOT need to stop after calling this; finish your current work naturally."
    )
    input_model = RequestReplanInput

    def __init__(
        self,
        *,
        task_id: str = "",
        run_id: str = "",
        store: Any = None,
        plan: Any = None,
        agent_session_id: str | None = None,
        trigger_dispatch_fn: Callable[[], None] | None = None,
    ) -> None:
        self._task_id = task_id
        self._run_id = run_id
        self._store = store
        self._plan = plan
        self._agent_session_id = agent_session_id
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

        # Persist as task artifact for the replanner to read
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

        # Spawn replanner task
        replanner_spawned = False
        if self._plan is not None:
            replanner_spawned = self._spawn_replanner_task(
                arguments.reason, arguments.context, arguments.suggestion
            )

        if not replanner_spawned and self._plan is None:
            logger.warning(
                "Replan requested by task %s (run=%s) but no plan bound — "
                "artifact saved but replanner task not spawned",
                self._task_id,
                self._run_id,
            )

        # Trigger dispatch so the replanner starts immediately
        if replanner_spawned and self._trigger_dispatch_fn is not None:
            try:
                self._trigger_dispatch_fn()
            except Exception:
                logger.debug(
                    "Failed to trigger immediate dispatch for replanner (run=%s)",
                    self._run_id,
                    exc_info=True,
                )

        return ToolResult(
            output="Replan request recorded and replanner task spawned. "
            "Continue your current work — no need to stop.",
            metadata={"replanner_spawned": replanner_spawned},
        )

    def _spawn_replanner_task(self, reason: str, context: str, suggestion: str) -> bool:
        """Create a replanner task in the plan. Returns True if spawned."""
        # Dedup: only one active replanner per plan
        for task in self._plan.tasks.values():
            if task.role == "replanner" and task.status in ("pending", "running"):
                logger.info(
                    "Replan requested by task %s but replanner already active",
                    self._task_id,
                )
                return False

        replanner_agent = self._plan.replanner_agent
        if not replanner_agent:
            logger.warning(
                "Replan requested by task %s but no replanner_agent configured",
                self._task_id,
            )
            return False

        replanner_task_id = f"replan-from-{self._task_id}"
        if replanner_task_id in self._plan.tasks:
            return False

        # Build replanner task description with full context
        desc_parts = [
            f"Replanning triggered by {self._task_id}.",
            f"REASON: {reason}",
            f"CONTEXT:\n{context}",
        ]
        if suggestion:
            desc_parts.append(f"SUGGESTION: {suggestion}")
        desc_parts.append(
            "Use read_task_board() to see the full task graph state. "
            "Call update_plan to add fix tasks and/or cancel pending tasks."
        )

        # Import coordination models for task creation
        try:
            from ephemeralos.swarm.models import TaskCIPlan, TeamTask
        except ImportError:
            logger.warning("Cannot import swarm models to spawn replanner task")
            return False

        replanner_task = TeamTask(
            task_id=replanner_task_id,
            description="\n\n".join(desc_parts),
            agent_name=replanner_agent,
            role="replanner",
            depends_on=[],
            ci_plan=TaskCIPlan(touches_paths=["__replanning__"]),
        )
        self._plan.tasks[replanner_task_id] = replanner_task

        # Persist to store
        if self._store is not None and getattr(self._store, "is_available", False):
            try:
                add_fn = getattr(self._store, "add_tasks_to_running_plan", None)
                if callable(add_fn):
                    add_fn(self._run_id, [{
                        "task_id": replanner_task_id,
                        "description": replanner_task.description,
                        "agent_name": replanner_agent,
                        "role": "replanner",
                        "depends_on": [],
                        "touches_paths": ["__replanning__"],
                    }])
            except Exception:
                logger.warning(
                    "Failed to persist replanner task %s (run=%s)",
                    replanner_task_id,
                    self._run_id,
                    exc_info=True,
                )

        logger.info(
            "Replan requested by task %s: spawned replanner %s",
            self._task_id,
            replanner_task_id,
        )
        return True
