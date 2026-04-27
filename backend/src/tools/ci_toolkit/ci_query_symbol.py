"""Code-intelligence symbol query tool."""

from __future__ import annotations

from tools.ci_toolkit._query_runtime import (
    CiQuerySymbolInput,
    CiQuerySymbolOutput,
    run_ci_query_symbol,
)
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool


@tool(
    name="ci_query_symbol",
    description=(
        "Find where a function, class, variable, or other named symbol is defined, and "
        "optionally all its reference sites. Prefer this over `grep` whenever you have a symbol "
        "name — returns true definitions and usages, not text matches. Use `grep` only for "
        "free-form strings or substrings that aren't valid identifiers."
    ),
    short_description="Find symbol definitions and references.",
    input_model=CiQuerySymbolInput,
    output_model=CiQuerySymbolOutput,
)
async def ci_query_symbol(
    query: str,
    kind: str = "",
    references: bool = False,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Search for symbol definitions and optionally trace references."""
    return await run_ci_query_symbol(
        query=query,
        kind=kind,
        references=references,
        context=context,
    )


__all__ = ["ci_query_symbol"]
