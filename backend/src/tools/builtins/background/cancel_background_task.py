"""Built-in tool for cancelling background tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class CancelBackgroundTaskInput(BaseModel):
    """Input for cancel_background_task tool."""
    task_id: str = Field(
        description="The task ID of the background task to cancel.",
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
        "Use check_background_progress first to find the task ID."
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
        cancelled = await manager.cancel(arguments.task_id, arguments.reason)

        if cancelled:
            reason_msg = f" Reason: {arguments.reason}" if arguments.reason else ""
            return ToolResult(
                output=f"Background task {arguments.task_id} cancelled.{reason_msg}",
                is_error=False,
            )

        return ToolResult(
            output=f"Could not cancel task {arguments.task_id}. "
            "It may have already completed or does not exist.",
            is_error=True,
        )
