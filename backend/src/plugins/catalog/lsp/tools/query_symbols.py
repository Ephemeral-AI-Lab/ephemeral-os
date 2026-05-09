"""lsp.query_symbols - Pyright workspace symbol search."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sandbox.plugin import call_plugin
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.core.results import TextToolOutput
from tools.sandbox_toolkit.session import resolve_sandbox_path


class QuerySymbolsInput(BaseModel):
    query: str = Field(..., description="Symbol name fragment.")
    file_path: str | None = Field(
        default=None,
        description="Optional file path to restrict the search to one document.",
    )


@tool(
    name="lsp.query_symbols",
    description="Return workspace or per-file Python symbol matches for the given query fragment.",
    short_description="LSP query symbols.",
    input_model=QuerySymbolsInput,
    output_model=TextToolOutput,
)
async def query_symbols(
    query: str,
    file_path: str | None = None,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    payload: dict[str, object] = {"query": query}
    if file_path is not None:
        payload["file_path"] = resolve_sandbox_path(file_path, context)
    return await call_plugin(
        context,
        plugin="lsp",
        op="query_symbols",
        payload=payload,
    )
