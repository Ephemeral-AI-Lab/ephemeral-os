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
    wait_for_diagnostics: bool = Field(
        False,
        description=(
            "When true, wait for at least one Pyright diagnostic before returning, "
            "up to the session diagnostic timeout."
        ),
    )


@tool(
    name="lsp.diagnostics",
    description="Return Pyright diagnostics (errors, warnings, hints) for a Python file.",
    short_description="LSP diagnostics.",
    input_model=DiagnosticsInput,
    output_model=TextToolOutput,
)
async def diagnostics(
    file_path: str,
    wait_for_diagnostics: bool = False,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    return await call_plugin(
        context,
        plugin="lsp",
        op="diagnostics",
        payload={
            "file_path": resolve_sandbox_path(file_path, context),
            "wait_for_diagnostics": wait_for_diagnostics,
        },
    )
