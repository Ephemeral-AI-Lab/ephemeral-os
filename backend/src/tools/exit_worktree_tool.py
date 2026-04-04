"""Tool for removing git worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ExitWorktreeToolInput(BaseModel):
    """Arguments for worktree removal."""

    path: str = Field(description="Worktree path to remove")


class ExitWorktreeTool(BaseTool):
    """Remove a git worktree."""

    name = "exit_worktree"
    description = "Remove a git worktree by path."
    input_model = ExitWorktreeToolInput

    async def execute(
        self,
        arguments: ExitWorktreeToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = Path(arguments.path).expanduser()
        if not path.is_absolute():
            path = (context.cwd / path).resolve()
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=context.cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        output = (result.stdout or result.stderr).strip() or f"Removed worktree {path}"
        return ToolResult(output=output, is_error=result.returncode != 0)
