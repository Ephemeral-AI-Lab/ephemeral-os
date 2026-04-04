"""Tool for triggering local named jobs on demand."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from ephemeralos.services.cron import get_cron_job
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class RemoteTriggerToolInput(BaseModel):
    """Arguments for triggering a local named job."""

    name: str = Field(description="Cron job name")
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class RemoteTriggerTool(BaseTool):
    """Run a registered cron job immediately."""

    name = "remote_trigger"
    description = "Trigger a configured local cron-style job immediately."
    input_model = RemoteTriggerToolInput

    async def execute(
        self,
        arguments: RemoteTriggerToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        job = get_cron_job(arguments.name)
        if job is None:
            return ToolResult(output=f"Cron job not found: {arguments.name}", is_error=True)

        cwd = Path(job.get("cwd") or context.cwd).expanduser()
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            str(job["command"]),
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=arguments.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(
                output=f"Remote trigger timed out after {arguments.timeout_seconds} seconds",
                is_error=True,
            )

        parts = []
        if stdout:
            parts.append(stdout.decode("utf-8", errors="replace").rstrip())
        if stderr:
            parts.append(stderr.decode("utf-8", errors="replace").rstrip())
        body = "\n".join(part for part in parts if part).strip() or "(no output)"
        return ToolResult(
            output=f"Triggered {arguments.name}\n{body}",
            is_error=process.returncode != 0,
            metadata={"returncode": process.returncode},
        )
