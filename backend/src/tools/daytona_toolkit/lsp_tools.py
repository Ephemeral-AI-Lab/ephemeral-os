"""LSP query tools for Daytona sandboxes."""

from __future__ import annotations

import json
import logging

from tools.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit.ci_integration import get_ci_service
from tools.decorator import tool

logger = logging.getLogger(__name__)


# -- Hover --------------------------------------------------------------------

@tool(name="daytona_lsp_hover", description="Get type information and documentation for a symbol at a position.", read_only=True)
async def daytona_lsp_hover(
    file_path: str,
    line: int,
    character: int = 0,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Get type, signature, and docstring for a symbol.

    Args:
        file_path: Path to the file
        line: 1-based line number
        character: 0-based character offset

    Returns:
        content (str): Hover content (type info, docstring)
        language (str): Language of the content
    """
    svc = get_ci_service(context)
    if svc is None:
        return ToolResult(output="LSP not available", is_error=True)

    result = svc.hover(file_path, line, character)
    if result is None:
        return ToolResult(output=f"No hover information at {file_path}:{line}")

    return ToolResult(output=json.dumps({
        "content": result.content,
        "language": result.language,
    }))


# -- Goto Definition ----------------------------------------------------------

@tool(name="daytona_lsp_definition", description="Find the definition location of a symbol.", read_only=True)
async def daytona_lsp_definition(
    file_path: str,
    line: int,
    character: int = 0,
    symbol: str = "",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Jump to the definition of a symbol across files.

    Args:
        file_path: Path to the file
        line: 1-based line number
        character: 0-based character offset
        symbol: Symbol name (optional, extracted from position if empty)

    Returns:
        definitions (list): Found definitions with name, kind, file_path, line, character, signature
    """
    svc = get_ci_service(context)
    if svc is None:
        return ToolResult(output="LSP not available", is_error=True)

    results = svc.find_definitions(
        file_path,
        symbol,
        line,
        character,
    )
    if not results:
        return ToolResult(output=f"No definitions found for symbol at {file_path}:{line}")

    defs = []
    for sym in results:
        defs.append({
            "name": sym.name,
            "kind": sym.kind.value if hasattr(sym.kind, "value") else str(sym.kind),
            "file_path": sym.file_path,
            "line": sym.line,
            "character": sym.character,
            "signature": sym.signature,
        })

    return ToolResult(output=json.dumps({"definitions": defs}))


# -- Find References ----------------------------------------------------------

@tool(name="daytona_lsp_references", description="Find all usages/references of a symbol across files.", read_only=True)
async def daytona_lsp_references(
    file_path: str,
    line: int,
    character: int = 0,
    symbol: str = "",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Find all references to a symbol across the codebase.

    Args:
        file_path: Path to the file
        line: 1-based line number
        character: 0-based character offset
        symbol: Symbol name (optional)

    Returns:
        references (list): Found references with file_path, line, character, text
        total_references (int): Total references found
    """
    svc = get_ci_service(context)
    if svc is None:
        return ToolResult(output="LSP not available", is_error=True)

    results = svc.find_references(
        file_path,
        symbol,
        line,
        character,
    )
    if not results:
        return ToolResult(output=f"No references found at {file_path}:{line}")

    refs = []
    for ref in results[:50]:
        refs.append({
            "file_path": ref.file_path,
            "line": ref.line,
            "character": ref.character,
            "text": ref.text,
        })

    return ToolResult(output=json.dumps({
        "references": refs,
        "total_references": len(results),
    }))


# -- Diagnostics --------------------------------------------------------------

@tool(name="daytona_lsp_diagnostics", description="Check a file for syntax errors, type errors, and warnings.", read_only=True)
async def daytona_lsp_diagnostics(
    file_path: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Get syntax and semantic diagnostics for a file.

    Args:
        file_path: Path to the file to check

    Returns:
        file_path (str): File that was checked
        diagnostics (list): Diagnostic items with line, character, severity, message, source
        clean (bool): True if no diagnostics were found
    """
    svc = get_ci_service(context)
    if svc is None:
        return ToolResult(output="LSP not available", is_error=True)

    results = svc.diagnostics(file_path)
    if not results:
        return ToolResult(output=json.dumps({
            "file_path": file_path, "diagnostics": [], "clean": True,
        }))

    diags = []
    for d in results:
        diags.append({
            "line": d.line,
            "character": d.character,
            "severity": d.severity.value if hasattr(d.severity, "value") else str(d.severity),
            "message": d.message,
            "source": d.source,
        })

    return ToolResult(output=json.dumps({
        "file_path": file_path, "diagnostics": diags, "clean": False,
    }))
