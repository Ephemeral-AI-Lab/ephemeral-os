"""Lightweight code intelligence tool for Python workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ephemeralos.services.lsp import (
    find_references,
    go_to_definition,
    hover,
    list_document_symbols,
    workspace_symbol_search,
)
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class LspToolInput(BaseModel):
    """Arguments for code intelligence queries."""

    operation: Literal[
        "document_symbol",
        "workspace_symbol",
        "go_to_definition",
        "find_references",
        "hover",
    ] = Field(description="The code intelligence operation to perform")
    file_path: str | None = Field(default=None, description="Path to the source file for file-based operations")
    symbol: str | None = Field(default=None, description="Explicit symbol name to look up")
    line: int | None = Field(default=None, ge=1, description="1-based line number for position-based lookups")
    character: int | None = Field(default=None, ge=1, description="1-based character offset for position-based lookups")
    query: str | None = Field(default=None, description="Substring query for workspace_symbol")

    @model_validator(mode="after")
    def validate_arguments(self) -> "LspToolInput":
        if self.operation == "workspace_symbol":
            if not self.query:
                raise ValueError("workspace_symbol requires query")
            return self
        if not self.file_path:
            raise ValueError(f"{self.operation} requires file_path")
        if self.operation == "document_symbol":
            return self
        if not self.symbol and self.line is None:
            raise ValueError(f"{self.operation} requires symbol or line")
        return self


class LspTool(BaseTool):
    """Read-only code intelligence for Python source files."""

    name = "lsp"
    description = (
        "Inspect Python code symbols, definitions, references, and hover information "
        "across the current workspace."
    )
    input_model = LspToolInput

    def is_read_only(self, arguments: LspToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: LspToolInput, context: ToolExecutionContext) -> ToolResult:
        root = context.cwd.resolve()
        if arguments.operation == "workspace_symbol":
            results = workspace_symbol_search(root, arguments.query or "")
            return ToolResult(output=_format_symbol_locations(results, root))

        assert arguments.file_path is not None  # validated above
        file_path = _resolve_path(root, arguments.file_path)
        if not file_path.exists():
            return ToolResult(output=f"File not found: {file_path}", is_error=True)
        if file_path.suffix != ".py":
            return ToolResult(output="The lsp tool currently supports Python files only.", is_error=True)

        if arguments.operation == "document_symbol":
            return ToolResult(output=_format_symbol_locations(list_document_symbols(file_path), root))

        if arguments.operation == "go_to_definition":
            results = go_to_definition(
                root=root,
                file_path=file_path,
                symbol=arguments.symbol,
                line=arguments.line,
                character=arguments.character,
            )
            return ToolResult(output=_format_symbol_locations(results, root))

        if arguments.operation == "find_references":
            results = find_references(
                root=root,
                file_path=file_path,
                symbol=arguments.symbol,
                line=arguments.line,
                character=arguments.character,
            )
            return ToolResult(output=_format_references(results, root))

        result = hover(
            root=root,
            file_path=file_path,
            symbol=arguments.symbol,
            line=arguments.line,
            character=arguments.character,
        )
        if result is None:
            return ToolResult(output="(no hover result)")
        parts = [
            f"{result.kind} {result.name}",
            f"path: {_display_path(result.path, root)}:{result.line}:{result.character}",
        ]
        if result.signature:
            parts.append(f"signature: {result.signature}")
        if result.docstring:
            parts.append(f"docstring: {result.docstring.strip()}")
        return ToolResult(output="\n".join(parts))


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _format_symbol_locations(results, root: Path) -> str:
    if not results:
        return "(no results)"
    lines = []
    for item in results:
        lines.append(
            f"{item.kind} {item.name} - {_display_path(item.path, root)}:{item.line}:{item.character}"
        )
        if item.signature:
            lines.append(f"  signature: {item.signature}")
        if item.docstring:
            lines.append(f"  docstring: {item.docstring.strip()}")
    return "\n".join(lines)


def _format_references(results: list[tuple[Path, int, str]], root: Path) -> str:
    if not results:
        return "(no results)"
    return "\n".join(f"{_display_path(path, root)}:{line}:{text}" for path, line, text in results)

