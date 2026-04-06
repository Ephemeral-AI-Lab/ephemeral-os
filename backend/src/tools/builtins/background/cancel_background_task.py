"""Built-in tool for cancelling background tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class CancelBackgroundTaskInput(BaseModel):
    """Input for cancel_background_task tool."""
    task_id: str | None = Field(
        default=None,
        description=(
            "Task ID to cancel. Copy the exact value from the `task_id` field in "
            "`check_background_progress` output. If omitted and exactly one "
            "background task is running, that task is cancelled. If multiple tasks "
            "are running, the call fails with a listing of running task IDs. "
            "Never pass null/None when multiple tasks are running — always specify."
        ),
    )
    reason: str = Field(
        default="",
        description="Optional reason for cancellation.",
    )


class CancelBackgroundTaskTool(BaseTool):
    """Cancel a running background task.

    Stops the specified background task. The task will be marked as
    cancelled and its partial output (if any) will be available via
    check_background_progress.
    """

    name: str = "cancel_background_task"
    description: str = (
        "Cancel a running background task by its task ID. "
        "Use check_background_progress first to find the task ID. "
        "If exactly one task is running, task_id may be omitted."
    )
    input_model: type[BaseModel] = CancelBackgroundTaskInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        manager = context.metadata.get("background_task_manager")
        if manager is None:
            return ToolResult(
                output="No background task manager available.",
                is_error=True,
            )

        assert isinstance(arguments, CancelBackgroundTaskInput)

        task_id = arguments.task_id

        # Disambiguation guard (mirrors wait_for_background_task):
        # the LLM frequently drops task_id when it thinks the intent is obvious.
        # Rather than crashing on ValidationError, auto-select the sole running
        # task or return an informative listing.
        if not task_id:
            snapshot = manager.get_status()
            running = [s for s in snapshot if s.get("status") == "running"]
            if len(running) == 0:
                return ToolResult(
                    output="No background tasks are running — nothing to cancel.",
                    is_error=False,
                )
            if len(running) == 1:
                task_id = running[0]["task_id"]
            else:
                listing = "\n".join(
                    f"  - task_id=\"{s['task_id']}\"  ({s.get('task_note') or s.get('tool_name')})"
                    for s in running
                )
                return ToolResult(
                    output=(
                        "ERROR: multiple background tasks are running and `task_id` "
                        "was not provided. You MUST copy one of the exact task_id "
                        "strings below into the `task_id` argument.\n"
                        f"Running tasks:\n{listing}\n"
                        "Example: cancel_background_task(task_id=\"<one of the above>\", reason=\"...\")"
                    ),
                    is_error=True,
                )

        cancelled = await manager.cancel(task_id, arguments.reason)

        if cancelled:
            reason_msg = f" Reason: {arguments.reason}" if arguments.reason else ""
            return ToolResult(
                output=f"Background task {task_id} cancelled.{reason_msg}",
                is_error=False,
            )

        return ToolResult(
            output=f"Could not cancel task {task_id}. "
            "It may have already completed or does not exist.",
            is_error=True,
        )
