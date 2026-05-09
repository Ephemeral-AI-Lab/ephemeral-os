"""lsp.diagnostics - Pyright diagnostics for a Python file."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sandbox.plugin import call_plugin
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.core.results import TextToolOutput
from tools.sandbox_toolkit.session import resolve_sandbox_path


class DiagnosticsInput(BaseModel):
    file_path: str = Field(..., description="Repo-relative or absolute file path.")


@tool(
    name="lsp.diagnostics",
    description="Return Pyright diagnostics (errors, warnings, hints) for a Python file.",
    short_description="LSP diagnostics.",
    input_model=DiagnosticsInput,
    output_model=TextToolOutput,
)
async def diagnostics(
    file_path: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    return await call_plugin(
        context,
        plugin="lsp",
        op="diagnostics",
        payload={"file_path": resolve_sandbox_path(file_path, context)},
    )
