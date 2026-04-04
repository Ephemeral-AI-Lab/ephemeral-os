"""Tool for maintaining a project TODO file."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TodoWriteToolInput(BaseModel):
    """Arguments for TODO writes."""

    item: str = Field(description="TODO item text")
    checked: bool = Field(default=False)
    path: str = Field(default="TODO.md")


class TodoWriteTool(BaseTool):
    """Append an item to a TODO markdown file."""

    name = "todo_write"
    description = "Append a TODO item to a markdown checklist file."
    input_model = TodoWriteToolInput

    async def execute(self, arguments: TodoWriteToolInput, context: ToolExecutionContext) -> ToolResult:
        path = Path(context.cwd) / arguments.path
        prefix = "- [x]" if arguments.checked else "- [ ]"
        existing = path.read_text(encoding="utf-8") if path.exists() else "# TODO\n"
        updated = existing.rstrip() + f"\n{prefix} {arguments.item}\n"
        path.write_text(updated, encoding="utf-8")
        return ToolResult(output=f"Updated {path}")
