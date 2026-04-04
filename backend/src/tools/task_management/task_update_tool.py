"""Tool for updating background task metadata."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tasks.manager import get_task_manager
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskUpdateToolInput(BaseModel):
    """Arguments for task updates."""

    task_id: str = Field(description="Task identifier")
    description: str | None = Field(default=None, description="Updated task description")
    progress: int | None = Field(default=None, ge=0, le=100, description="Progress percentage")
    status_note: str | None = Field(default=None, description="Short human-readable task note")


class TaskUpdateTool(BaseTool):
    """Update task metadata for progress tracking."""

    name = "task_update"
    description = "Update a task description, progress, or status note."
    input_model = TaskUpdateToolInput

    async def execute(
        self,
        arguments: TaskUpdateToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        try:
            task = get_task_manager().update_task(
                arguments.task_id,
                description=arguments.description,
                progress=arguments.progress,
                status_note=arguments.status_note,
            )
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        parts = [f"Updated task {task.id}"]
        if arguments.description:
            parts.append(f"description={task.description}")
        if arguments.progress is not None:
            parts.append(f"progress={task.metadata.get('progress', '')}%")
        if arguments.status_note:
            parts.append(f"note={task.metadata.get('status_note', '')}")
        return ToolResult(output=" ".join(parts))
