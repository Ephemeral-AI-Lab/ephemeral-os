"""Tool for Language Server Protocol operations."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class LspToolInput(BaseModel):
    """Arguments for LSP operations."""

    action: str = Field(description="LSP action: hover, definition, references, symbols, diagnostics")
    file_path: str = Field(description="Path to the file to analyse")
    line: int | None = Field(default=None, description="1-based line number (for hover/definition/references)")
    character: int | None = Field(default=None, description="0-based character offset (for hover/definition/references)")
    query: str | None = Field(default=None, description="Symbol query string (for symbols action)")


class LspTool(BaseTool):
    """Query a Language Server for code intelligence (hover, go-to-definition, references, symbols, diagnostics)."""

    name = "lsp"
    description = "Query a Language Server for code intelligence operations."
    input_model = LspToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: LspToolInput, context: ToolExecutionContext) -> ToolResult:
        # Stub implementation — to be connected to a real LSP client.
        return ToolResult(
            output=f"LSP {arguments.action} on {arguments.file_path} (not yet connected to a language server)",
            metadata={"action": arguments.action, "file": arguments.file_path},
        )
