"""Daytona-backed cross-file symbol rename tool.

``daytona_rename_symbol(symbol, new_name, kind=?, file_hint=?)`` resolves
a symbol via the workspace :class:`SymbolIndex`, returns
``status="ambiguous"`` with candidates when a name matches multiple
places, and otherwise runs the rename through the OCC-gated
code-intelligence commit path (``svc.rename_symbol``). No shell, no
dry-run, no audit-only side path.
"""

from __future__ import annotations

import json
import keyword
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from code_intelligence._async_bridge import run_sync_in_executor, use_sandbox_io_loop
from code_intelligence.types import SymbolKind
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_attribution import rebind_ci_service, resolved_agent_id
from tools.core.ci_runtime import ci_required_result, get_ci_service
from tools.core.decorator import tool
from tools.core.op_result_to_tool_result import operation_result_to_tool_result
from tools.core.sandbox_runtime import resolve_daytona_path
from tools.daytona_toolkit._daytona_utils import (
    _scope_deny_message,
    _team_repo_scope_deny_errors,
    _team_repo_write_error,
)

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = r"^[A-Za-z_][A-Za-z0-9_]*$"
_CANDIDATE_LIMIT = 10


class FileRenameSummary(BaseModel):
    file_path: str = Field(..., description="Absolute path of the changed file.")
    status: str = Field(..., description="`renamed` or `failed`.")
    message: str | None = Field(default=None, description="Failure reason when status=failed.")


class CandidateSymbol(BaseModel):
    name: str
    kind: str
    file_path: str
    line: int
    container: str = ""
    signature: str = ""


class DaytonaRenameSymbolsOutput(BaseModel):
    status: str = Field(
        ...,
        description=(
            "`renamed`, `no_changes`, `ambiguous` (multiple matches — nothing "
            "written), `no_match`, `aborted_version`, `aborted_overlap`, "
            "`aborted_lock`, or `failed`."
        ),
    )
    new_name: str = Field(..., description="Requested new identifier.")
    files: list[FileRenameSummary] = Field(
        default_factory=list,
        description="Per-file rename outcome (one entry per touched file).",
    )
    candidates: list[CandidateSymbol] = Field(
        default_factory=list,
        description="Populated only when status=='ambiguous'; up to 10 entries.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Advisory warnings (e.g., files outside write_scope).",
    )
    message: str | None = Field(default=None, description="Top-level status message.")


class DaytonaRenameSymbolsInput(BaseModel):
    symbol: str = Field(
        ...,
        min_length=1,
        description=(
            "Target symbol name; may be dotted (e.g. `Foo.bar`, "
            "`module.func`) to disambiguate a method from a module-level "
            "function with the same leaf name."
        ),
    )
    new_name: str = Field(..., min_length=1, description="New identifier to rename to.")
    kind: SymbolKind | None = Field(
        default=None,
        description=(
            "Optional disambiguator: `function`, `class`, `method`, "
            "`variable`. Narrows candidates before the ambiguity check."
        ),
    )
    file_hint: str | None = Field(
        default=None,
        description=(
            "Optional substring match against the absolute file path "
            "(e.g. `backend/src/foo/`) to narrow candidates."
        ),
    )


# -- Helpers ----------------------------------------------------------------


def _validate_new_name(new_name: str) -> str | None:
    if not re.match(_IDENTIFIER_RE, new_name):
        return f"Invalid identifier: {new_name!r}. Must match {_IDENTIFIER_RE}."
    if keyword.iskeyword(new_name):
        return f"Cannot rename to Python keyword: {new_name!r}."
    return None


def _candidate_payload(sym: Any) -> dict[str, Any]:
    return {
        "name": str(getattr(sym, "name", "")),
        "kind": str(getattr(getattr(sym, "kind", ""), "value", getattr(sym, "kind", ""))),
        "file_path": str(getattr(sym, "file_path", "")),
        "line": int(getattr(sym, "line", 0) or 0),
        "container": str(getattr(sym, "container", "") or ""),
        "signature": str(getattr(sym, "signature", "") or ""),
    }


def _symbol_name_column(sym: Any) -> int:
    """Best-effort column for the symbol name, not the declaration keyword."""
    indexed_column = int(getattr(sym, "character", 0) or 0)
    kind = getattr(sym, "kind", None)
    signature = str(getattr(sym, "signature", "") or "")
    if kind in {SymbolKind.FUNCTION, SymbolKind.METHOD} and signature.startswith("def "):
        return indexed_column + len("def ")
    if kind is SymbolKind.CLASS and signature.startswith("class "):
        return indexed_column + len("class ")
    return indexed_column


def _resolve_symbol(
    svc: Any,
    *,
    symbol: str,
    kind: SymbolKind | None,
    file_hint: str | None,
) -> list[Any]:
    """Resolve *symbol* via the workspace symbol index.

    Supports dotted names (``Foo.bar`` — leaf ``bar`` filtered by
    container ``Foo``) and optional ``kind``/``file_hint`` narrowing.
    """
    parent: str | None = None
    leaf = symbol
    if "." in symbol:
        parent, _, leaf = symbol.rpartition(".")
    symbol_index = getattr(svc, "symbol_index", None)
    if symbol_index is None:
        return []
    try:
        symbol_index.ensure_built(wait=True)
    except Exception:  # pragma: no cover - defensive
        logger.debug("symbol_index.ensure_built failed", exc_info=True)
    try:
        raw = symbol_index.find(leaf, kind=kind)
    except Exception:  # pragma: no cover - defensive
        logger.debug("symbol_index.find failed", exc_info=True)
        return []

    matches = [m for m in raw if getattr(m, "name", "") == leaf]
    if parent is not None:
        matches = [m for m in matches if str(getattr(m, "container", "")) == parent]
    if file_hint:
        matches = [m for m in matches if file_hint in str(getattr(m, "file_path", ""))]
    return matches


async def _perform_rename(
    *,
    svc: Any,
    context: ToolExecutionContext,
    resolved_path: str,
    line: int,
    character: int,
    new_name: str,
    extra_warnings: list[str] | None = None,
) -> ToolResult:
    """Run a rename through the OCC-gated service primitive.

    Resolves the plan through :meth:`rename_symbol_plan` first so we can
    apply write-scope policy per affected file before any commit, then
    submits the whole rename as one OCC batch.
    """
    try:
        with use_sandbox_io_loop():
            plan = await run_sync_in_executor(
                svc.rename_symbol_plan,
                resolved_path,
                int(line),
                int(character),
                new_name,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("rename_symbol_plan raised for %s", resolved_path, exc_info=True)
        return ToolResult(output=f"LSP rename failed: {exc}", is_error=True)

    changes = getattr(plan, "changes", ()) or ()
    warnings = list(extra_warnings or [])
    if not changes:
        return ToolResult(
            output=json.dumps(
                {
                    "status": "no_changes",
                    "new_name": new_name,
                    "files": [],
                    "warnings": warnings,
                    "message": (
                        f"No rename changes produced for {resolved_path}:{line}. "
                        "Confirm the position points to a valid symbol and that "
                        "`new_name` is not already in use."
                    ),
                }
            ),
        )

    # Scope policy is inline because plan paths surface only after
    # rename_symbol_plan. Test-file block and outside-scope deny are
    # independent checks — test files may land inside write_scope and
    # must still fail that precedence-ordered check.
    test_file_errors: list[str] = []
    for change in changes:
        err = _team_repo_write_error(
            context, change.file_path, tool_name="daytona_rename_symbol",
        )
        if err is not None:
            test_file_errors.append(err)
    if test_file_errors:
        return ToolResult(
            output=(
                "Rename blocked by write-scope policy:\n  - "
                + "\n  - ".join(test_file_errors)
            ),
            is_error=True,
        )

    scope_offenders = _team_repo_scope_deny_errors(
        context,
        [change.file_path for change in changes],
        tool_name="daytona_rename_symbol",
    )
    if scope_offenders:
        return ToolResult(
            output=_scope_deny_message(
                scope_offenders, tool_name="daytona_rename_symbol",
            ),
            is_error=True,
        )

    rebind_ci_service(context, svc)
    with use_sandbox_io_loop():
        result = await run_sync_in_executor(
            svc.rename_symbol,
            resolved_path,
            int(line),
            int(character),
            new_name,
            agent_id=resolved_agent_id(context),
            description=f"rename to {new_name}",
        )

    primary_paths = [change.file_path for change in changes]
    return operation_result_to_tool_result(
        result,
        tool_name="daytona_rename_symbol",
        success_status="renamed",
        primary_paths=primary_paths,
        warnings=warnings,
        success_extra={
            "new_name": new_name,
            "files": [
                {"file_path": path, "status": "renamed"}
                for path in primary_paths
            ],
        },
    )


@tool(
    name="daytona_rename_symbol",
    description=(
        "Rename a Python symbol by name across every file where it is referenced "
        "inside the Daytona sandbox, using LSP semantics. Resolves `symbol` via "
        "the workspace symbol index, supports dotted names (`Foo.bar` narrows to "
        "the `bar` method on class `Foo`), and returns `status=\"ambiguous\"` "
        "with candidates when the name is not unique. Commits the rewrite as one "
        "OCC batch. Python-only for now."
    ),
    short_description="Rename a symbol by name across every referencing file.",
    input_model=DaytonaRenameSymbolsInput,
    output_model=DaytonaRenameSymbolsOutput,
)
async def daytona_rename_symbol(
    symbol: str,
    new_name: str,
    kind: SymbolKind | None = None,
    file_hint: str | None = None,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Resolve *symbol* then run the rename through the OCC commit path."""
    svc = get_ci_service(context)
    if svc is None:
        return ci_required_result(
            "daytona_rename_symbol",
            "LSP rename is disabled without CI service.",
        )
    if not hasattr(svc, "rename_symbol_plan"):
        return ToolResult(output="LSP rename not available", is_error=True)
    invalid = _validate_new_name(new_name)
    if invalid is not None:
        return ToolResult(output=invalid, is_error=True)

    matches = _resolve_symbol(svc, symbol=symbol, kind=kind, file_hint=file_hint)

    if not matches:
        msg = (
            f"No symbol named {symbol!r} found in the workspace index. "
            "Try `ci_query_symbol` for discovery, or broaden the name."
        )
        return ToolResult(
            output=json.dumps(
                {
                    "status": "no_match",
                    "new_name": new_name,
                    "files": [],
                    "candidates": [],
                    "warnings": [],
                    "message": msg,
                }
            ),
            is_error=True,
        )

    if len(matches) > 1:
        truncated = matches[:_CANDIDATE_LIMIT]
        extra = len(matches) - len(truncated)
        more = f" (+{extra} more — refine with `kind` and/or `file_hint`)" if extra else ""
        msg = (
            f"{len(matches)} symbols match {symbol!r}. "
            f"Re-invoke with `kind=` or `file_hint=` to disambiguate.{more}"
        )
        return ToolResult(
            output=json.dumps(
                {
                    "status": "ambiguous",
                    "new_name": new_name,
                    "files": [],
                    "candidates": [_candidate_payload(m) for m in truncated],
                    "warnings": [],
                    "message": msg,
                }
            ),
            is_error=True,
            metadata={"candidate_count": len(matches)},
        )

    sym = matches[0]
    resolved_path = resolve_daytona_path(str(getattr(sym, "file_path", "")), context)
    pivot_line = int(getattr(sym, "line", 0) or 0)
    pivot_char = _symbol_name_column(sym)
    return await _perform_rename(
        svc=svc,
        context=context,
        resolved_path=resolved_path,
        line=pivot_line,
        character=pivot_char,
        new_name=new_name,
    )
