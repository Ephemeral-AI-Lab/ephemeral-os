"""Tool for deleting local cron jobs."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.services.cron import delete_cron_job
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class CronDeleteToolInput(BaseModel):
    """Arguments for deleting a cron job."""

    name: str = Field(description="Cron job name")


class CronDeleteTool(BaseTool):
    """Delete a local cron job."""

    name = "cron_delete"
    description = "Delete a local cron-style job by name."
    input_model = CronDeleteToolInput

    async def execute(
        self,
        arguments: CronDeleteToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        if not delete_cron_job(arguments.name):
            return ToolResult(output=f"Cron job not found: {arguments.name}", is_error=True)
        return ToolResult(output=f"Deleted cron job {arguments.name}")
