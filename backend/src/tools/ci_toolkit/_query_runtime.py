"""Shared runtime for CI query tool implementations."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, cast

from pydantic import BaseModel, Field

from sandbox.api.code_intelligence_api import CodeIntelligenceApi
from sandbox.api.models import (
    ReferencesRequest,
    SymbolDefinition,
    SymbolQueryRequest,
    WorkspaceStructureRequest,
)
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.sandbox_session import actor_from_context, code_intelligence_api_or_error


class CiStatusInput(BaseModel):
    include_edit_hotspots: bool = Field(
        default=True,
        description="Whether to include edit hotspot information.",
    )
    hotspot_limit: int = Field(
        default=10,
        ge=1,
        description="Maximum edit hotspot results when included.",
    )
    hotspot_cross_run: bool = Field(
        default=False,
        description="Query arbiter-backed cross-run contention history.",
    )


class CiStatusOutput(BaseModel):
    status: str | None = Field(
        default=None,
        description="Status string for unavailable code intelligence responses.",
    )
    reason: str | None = Field(
        default=None,
        description="Reason code intelligence is unavailable.",
    )
    sandbox_id: str | None = Field(default=None, description="Sandbox id.")
    initialized: bool | None = Field(
        default=None,
        description="Whether code intelligence is initialized.",
    )
    workspace_root: str | None = Field(default=None, description="Indexed workspace root.")
    symbol_index: dict[str, Any] | None = Field(
        default=None,
        description="Symbol index status payload.",
    )
    arbiter: dict[str, Any] | None = Field(
        default=None,
        description="Edit arbiter status payload.",
    )
    edit_buffer: dict[str, Any] | None = Field(
        default=None,
        description="Edit buffer status payload.",
    )
    lsp: dict[str, Any] | None = Field(default=None, description="LSP status payload.")
    edit_hotspots: dict[str, Any] | None = Field(
        default=None,
        description="Optional edit hotspot payload.",
    )


class CiWorkspaceStructureInput(BaseModel):
    path: str = Field(
        default="",
        description="Subdirectory to list; empty means workspace root.",
    )
    max_depth: int = Field(
        default=3,
        ge=0,
        description="Maximum directory depth to include.",
    )


class CiWorkspaceStructureOutput(BaseModel):
    status: str | None = Field(
        default=None,
        description="Status for unavailable or empty workspace-structure responses.",
    )
    reason: str | None = Field(
        default=None,
        description="Reason workspace structure is unavailable.",
    )
    source: str | None = Field(
        default=None,
        description="Source used for the path list: index, local, remote, or none.",
    )
    path: str = Field(default="", description="Requested path prefix.")
    max_depth: int | None = Field(default=None, description="Requested maximum depth.")
    paths: list[str] = Field(default_factory=list, description="Workspace paths.")
    rendered: str = Field(default="", description="Human-readable newline-delimited paths.")
    message: str | None = Field(default=None, description="Human-readable status message.")


class CiQuerySymbolInput(BaseModel):
    query: str = Field(
        ...,
        description="Symbol name, partial symbol name, or exact file path to search.",
    )
    kind: str = Field(
        default="",
        description="Optional symbol kind filter, such as function, class, method, or variable.",
    )
    references: bool = Field(
        default=False,
        description="Whether to trace callers and import sites for matching definitions.",
    )


class CiSymbolDefinitionOutput(BaseModel):
    name: str = Field(..., description="Symbol name.")
    kind: str = Field(..., description="Symbol kind.")
    file: str = Field(..., description="File containing the symbol.")
    line: int | None = Field(default=None, description="One-based symbol line number.")
    signature: str | None = Field(default=None, description="Symbol signature.")


class CiSymbolReferenceOutput(BaseModel):
    file: str = Field(..., description="Reference file path.")
    line: int | None = Field(default=None, description="One-based reference line number.")
    text: str = Field(default="", description="Reference line text.")


class CiQuerySymbolOutput(BaseModel):
    status: str | None = Field(
        default=None,
        description="Status for unavailable symbol-query responses.",
    )
    reason: str | None = Field(default=None, description="Reason symbol query is unavailable.")
    file: str | None = Field(default=None, description="File path for file-bootstrap queries.")
    definitions: list[CiSymbolDefinitionOutput] = Field(
        default_factory=list,
        description="Matching symbol definitions.",
    )
    references: list[CiSymbolReferenceOutput] = Field(
        default_factory=list,
        description="Reference sites when requested.",
    )
    total_references: int | None = Field(
        default=None,
        description="Total references collected.",
    )
    confidence: str | None = Field(default=None, description="Confidence level for references.")
    reference_status: str | None = Field(
        default=None,
        description="Reference source/status such as lsp or definition_fallback.",
    )
    lsp_reason: str | None = Field(
        default=None,
        description="Reason LSP references were unavailable when using a fallback.",
    )
    hint: str | None = Field(default=None, description="Follow-up guidance.")
    message: str | None = Field(default=None, description="Human-readable status message.")


def _normalize_symbol_query(query: str) -> str:
    normalized = str(query or "").strip().strip("`'\"")
    for prefix in ("async def ", "def ", "class ", "function "):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    if "(" in normalized:
        normalized = normalized.split("(", 1)[0].strip()
    if normalized.endswith(":"):
        normalized = normalized[:-1].strip()
    return normalized


def _api_or_error(
    context: ToolExecutionContextService,
) -> tuple[CodeIntelligenceApi | None, ToolResult | None]:
    api, err = code_intelligence_api_or_error(context)
    return cast(CodeIntelligenceApi | None, api), err


def _sandbox_id(context: ToolExecutionContextService) -> str:
    return str(context.get("sandbox_id") or "").strip()


def _record_symbol_navigation(context: ToolExecutionContextService) -> None:
    current = context.get("_ci_symbol_navigation_calls", 0)
    context["_ci_symbol_navigation_calls"] = (
        int(current) + 1 if isinstance(current, (int, float)) else 1
    )


async def run_ci_status(
    include_edit_hotspots: bool = True,
    hotspot_limit: int = 10,
    hotspot_cross_run: bool = False,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Check code intelligence service readiness."""
    del hotspot_limit, hotspot_cross_run
    api, err = _api_or_error(context)
    if err is not None:
        return err
    status = await api.status(_sandbox_id(context))
    payload = asdict(status)
    if include_edit_hotspots and payload.get("edit_hotspots") is None:
        payload["edit_hotspots"] = {
            "hotspots": [],
            "note": "Arbiter history not available",
        }
    elif not include_edit_hotspots:
        payload.pop("edit_hotspots", None)
    return ToolResult(output=json.dumps(payload, indent=2, default=str))


async def run_ci_workspace_structure(
    path: str = "",
    max_depth: int = 3,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """List workspace file structure."""
    api, err = _api_or_error(context)
    if err is not None:
        return err
    result = await api.workspace_structure(
        _sandbox_id(context),
        WorkspaceStructureRequest(
            actor=actor_from_context(context),
            path=path,
            max_depth=max_depth,
        ),
    )
    paths = list(result.paths)
    status = None
    message = None
    if not paths:
        status = "empty"
        message = (
            "No files indexed yet. Use `glob` for file discovery when the symbol index is cold."
        )
    payload = CiWorkspaceStructureOutput(
        status=status,
        source=result.source,
        path=path,
        max_depth=max_depth,
        paths=paths,
        rendered="\n".join(paths) if paths else "",
        message=message,
    )
    return ToolResult(output=payload.model_dump_json())


async def run_ci_query_symbol(
    query: str,
    kind: str = "",
    references: bool = False,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Search for symbol definitions and optionally trace references."""
    query = _normalize_symbol_query(query)
    api, err = _api_or_error(context)
    if err is not None:
        return err

    _record_symbol_navigation(context)
    result = await api.query_symbols(
        _sandbox_id(context),
        SymbolQueryRequest(
            query=query,
            kind=kind,
            include_references=references,
            actor=actor_from_context(context),
        ),
    )
    definitions = [_definition_payload(defn) for defn in result.definitions]
    if not definitions:
        payload = CiQuerySymbolOutput(
            definitions=[],
            references=[],
            total_references=0 if references else None,
            confidence="none",
            file=result.matched_file,
            message=f"No symbols matching '{query}'",
        )
        return ToolResult(output=payload.model_dump_json())

    if result.matched_file:
        payload: dict[str, Any] = {
            "file": result.matched_file,
            "definitions": definitions,
            "confidence": result.confidence or "file_symbols",
        }
        if references:
            payload["references"] = []
            payload["total_references"] = 0
            payload["hint"] = (
                "File-path bootstrap query. Use one of the returned symbol names with "
                "`references=true` to trace callers/import sites."
            )
        return ToolResult(output=json.dumps(payload, indent=2))

    if not references:
        return ToolResult(output=json.dumps({"definitions": definitions}, indent=2))

    ref_payloads, used_lsp = await _collect_references(
        api,
        _sandbox_id(context),
        result.definitions,
        context=context,
    )
    if not ref_payloads:
        ref_payloads = [
            {
                "file": defn.file_path,
                "line": defn.line,
                "text": f"definition: {defn.kind} {defn.name}",
            }
            for defn in result.definitions
        ]
    payload = {
        "definitions": definitions,
        "references": ref_payloads,
        "total_references": len(ref_payloads),
        "confidence": "full" if used_lsp else "unavailable",
        "reference_status": "lsp" if used_lsp else "definition_fallback",
    }
    if not used_lsp:
        payload["lsp_reason"] = "no_lsp_references"
    return ToolResult(output=json.dumps(payload, indent=2))


async def _collect_references(
    api: CodeIntelligenceApi,
    sandbox_id: str,
    definitions: tuple[SymbolDefinition, ...],
    *,
    context: ToolExecutionContextService,
) -> tuple[list[dict[str, Any]], bool]:
    refs: list[dict[str, Any]] = []
    for defn in definitions:
        result = await api.find_references(
            sandbox_id,
            ReferencesRequest(
                file_path=defn.file_path,
                symbol=defn.name,
                line=defn.line,
                character=defn.character,
                actor=actor_from_context(context),
            ),
        )
        refs.extend(
            {
                "file": ref.file_path,
                "line": ref.line,
                "text": ref.text,
            }
            for ref in result.references
        )
    return refs, bool(refs)


def _definition_payload(defn: SymbolDefinition) -> dict[str, Any]:
    return {
        "name": defn.name,
        "kind": defn.kind,
        "file": defn.file_path,
        "line": defn.line,
        "signature": defn.signature,
    }


__all__ = [
    "CiQuerySymbolInput",
    "CiQuerySymbolOutput",
    "CiStatusInput",
    "CiStatusOutput",
    "CiWorkspaceStructureInput",
    "CiWorkspaceStructureOutput",
    "run_ci_query_symbol",
    "run_ci_status",
    "run_ci_workspace_structure",
]
