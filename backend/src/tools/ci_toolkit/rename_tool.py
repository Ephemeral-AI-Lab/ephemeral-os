"""Cross-file symbol rename tools backed by the code intelligence LSP.

Exposes one tool:

* ``ci_rename_symbol(symbol, new_name, kind=?, file_hint=?)`` — resolves the
  symbol name via :class:`SymbolIndex`, returns ``status="ambiguous"`` with
  candidates when a name matches multiple places, and otherwise delegates to
  the operation OCC commit.

Both route through the shared OCC commit entry point: the whole rename lands
or none of it does — never leaves a half-renamed tree.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import logging
import re
import shlex
from typing import Any

from code_intelligence.types import EditResult, OperationChange, OperationResult, SymbolKind
from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import (
    commit_ci_operation,
    exec_ci_process_operation,
    finalize_ci_operation_result,
    get_ci_service,
)
from tools.core.decorator import tool
from tools.core.sandbox_runtime import resolve_daytona_path
from tools.daytona_toolkit._daytona_utils import (
    _extract_exit_code,
    _require_sandbox,
    _team_repo_write_error,
    _team_repo_write_warning,
    _wrap_bash_command,
    record_coordination_warning,
)

logger = logging.getLogger(__name__)

_DIFF_MAX_CHARS = 8000
_IDENTIFIER_RE = r"^[A-Za-z_][A-Za-z0-9_]*$"
_CANDIDATE_LIMIT = 10
_PROCESS_RENAME_TIMEOUT = 180
_PROCESS_RENAME_SCRIPT = r"""
import base64
import hashlib
import json
import os
import pathlib
import sys
import tempfile


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def fail(status: str, file_path: str, reason: str, exit_code: int = 2) -> None:
    print(json.dumps({"status": status, "conflict_file": file_path, "conflict_reason": reason}))
    raise SystemExit(exit_code)


payload = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
changes = payload.get("changes", [])
resolved = []
temps = []
try:
    for change in changes:
        file_path = str(change["file_path"])
        path = pathlib.Path(file_path)
        base_hash = str(change.get("base_hash") or "")
        base_existed = bool(change.get("base_existed", True))
        final_content = change.get("final_content")

        existed_now = path.exists()
        if base_existed and not existed_now:
            fail("aborted_version", file_path, "file was deleted since rename plan was built")
        if not base_existed and existed_now:
            fail("aborted_version", file_path, "file already exists; base said it did not")

        current = path.read_text(encoding="utf-8") if existed_now else ""
        current_hash = content_hash(current) if existed_now else ""
        if current_hash != base_hash:
            fail("aborted_version", file_path, "file content changed before rename commit")

        resolved.append((path, file_path, final_content, current_hash, existed_now))

    results = []
    for path, file_path, final_content, current_hash, existed_now in resolved:
        if final_content is None:
            if path.exists():
                path.unlink()
            new_hash = ""
        else:
            parent = path.parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
            temps.append(tmp)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(final_content))
            os.replace(tmp, path)
            temps.remove(tmp)
            new_hash = content_hash(str(final_content))
        results.append(
            {
                "file_path": file_path,
                "old_hash": current_hash if existed_now else "",
                "new_hash": new_hash,
            }
        )
except SystemExit:
    raise
except Exception as exc:
    print(json.dumps({"status": "failed", "conflict_file": "", "conflict_reason": str(exc)}))
    raise SystemExit(1)
finally:
    for tmp in list(temps):
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass

print(json.dumps({"status": "committed", "files": results}))
"""


# -- Shared schemas ---------------------------------------------------------


class FileRenameSummary(BaseModel):
    file_path: str = Field(..., description="Absolute path of the changed file.")
    status: str = Field(..., description="`renamed`, `dry_run`, or `failed`.")
    diff: str | None = Field(default=None, description="Unified diff for dry-run.")
    message: str | None = Field(default=None, description="Failure reason when status=failed.")


class CandidateSymbol(BaseModel):
    name: str
    kind: str
    file_path: str
    line: int
    container: str = ""
    signature: str = ""


class CiRenameSymbolOutput(BaseModel):
    status: str = Field(
        ...,
        description=(
            "`renamed`, `dry_run`, `no_changes`, `ambiguous` (multiple "
            "matches — nothing written), `no_match`, `aborted` (OCC/merge "
            "conflict — nothing written), or `failed`."
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


class CiRenameSymbolInput(BaseModel):
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
    dry_run: bool = Field(
        default=False,
        description="Preview the per-file diffs without writing anything.",
    )


# -- Helpers ----------------------------------------------------------------


def _validate_new_name(new_name: str) -> str | None:
    if not re.match(_IDENTIFIER_RE, new_name):
        return f"Invalid identifier: {new_name!r}. Must match {_IDENTIFIER_RE}."
    if new_name in {"None", "True", "False"}:
        return f"Cannot rename to Python keyword: {new_name!r}."
    return None


def _unified_diff(old: str, new: str, path: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if len(diff) > _DIFF_MAX_CHARS:
        diff = diff[:_DIFF_MAX_CHARS] + "\n... (truncated)"
    return diff


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


def _rename_process_command(changes: tuple[OperationChange, ...]) -> str:
    payload = {
        "changes": [
            {
                "file_path": change.file_path,
                "base_hash": change.base_hash,
                "base_existed": change.base_existed,
                "final_content": change.final_content,
            }
            for change in changes
        ],
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return _wrap_bash_command(
        f"python3 -c {shlex.quote(_PROCESS_RENAME_SCRIPT)} {shlex.quote(encoded)}"
    )


def _edit_result(
    file_path: str,
    message: str,
    *,
    success: bool = False,
    conflict: bool = False,
    conflict_reason: str = "",
    snapshot_id: str = "",
) -> EditResult:
    return EditResult(
        success=success,
        file_path=file_path,
        message=message,
        conflict=conflict,
        conflict_reason=conflict_reason,
        snapshot_id=snapshot_id,
    )


async def _commit_rename_via_process(
    *,
    svc: Any,
    context: ToolExecutionContext,
    changes: tuple[OperationChange, ...],
    agent_id: str,
    description: str,
) -> OperationResult:
    sandbox = await _require_sandbox(context)
    command = _rename_process_command(changes)
    try:
        response = await exec_ci_process_operation(
            context,
            sandbox,
            command,
            timeout=_PROCESS_RENAME_TIMEOUT,
            description=description,
        )
    except Exception as exc:
        return OperationResult(
            success=False,
            status="failed",
            files=tuple(_edit_result(change.file_path, str(exc)) for change in changes),
            conflict_file=None,
            conflict_reason=str(exc),
        )

    raw = str(getattr(response, "result", "") or "")
    cleaned, exit_code = _extract_exit_code(
        raw,
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    try:
        payload = json.loads(cleaned or "{}")
    except json.JSONDecodeError:
        return OperationResult(
            success=False,
            status="failed",
            files=tuple(
                _edit_result(change.file_path, cleaned or "rename process failed")
                for change in changes
            ),
            conflict_file=None,
            conflict_reason=cleaned or "rename process failed",
        )

    status = str(payload.get("status") or ("committed" if exit_code == 0 else "failed"))
    if exit_code != 0 or status != "committed":
        conflict_file = str(payload.get("conflict_file") or "")
        conflict_reason = str(payload.get("conflict_reason") or status or "rename process failed")
        if status.startswith("aborted"):
            try:
                svc.arbiter.record_conflict(status)
            except Exception:
                logger.debug("rename process conflict record failed", exc_info=True)
        result = OperationResult(
            success=False,
            status=(
                status
                if status in {"aborted_version", "aborted_overlap", "aborted_lock"}
                else "failed"
            ),  # type: ignore[arg-type]
            files=tuple(
                _edit_result(
                    change.file_path,
                    conflict_reason,
                    conflict=status.startswith("aborted"),
                    conflict_reason=status if status.startswith("aborted") else "",
                )
                for change in changes
            ),
            conflict_file=conflict_file or None,
            conflict_reason=conflict_reason,
        )
        finalize_ci_operation_result(
            context,
            result=result,
            changes=changes,
            edit_type="rename",
            description=description,
            ci_arbiter=getattr(svc, "arbiter", None),
        )
        return result

    returned_files = payload.get("files") if isinstance(payload, dict) else None
    by_path = {
        str(item.get("file_path")): item
        for item in (returned_files or [])
        if isinstance(item, dict) and item.get("file_path")
    }
    results: list[EditResult] = []
    for change in changes:
        item = by_path.get(change.file_path, {})
        old_hash = str(item.get("old_hash") or change.base_hash)
        new_hash = str(item.get("new_hash") or "")
        if not new_hash and change.final_content is not None:
            new_hash = hashlib.sha256(change.final_content.encode("utf-8")).hexdigest()[:16]
        gen = svc.arbiter.record_edit(
            file_path=change.file_path,
            actor_label=agent_id,
            edit_type="rename",
            old_hash=old_hash,
            new_hash=new_hash,
            description=description,
        )
        try:
            svc.symbol_index.refresh(change.file_path, change.final_content or "")
        except Exception:
            logger.debug("symbol refresh failed after process rename for %s", change.file_path, exc_info=True)
        try:
            svc.lsp_client.invalidate(change.file_path)
        except Exception:
            logger.debug("lsp invalidate failed after process rename for %s", change.file_path, exc_info=True)
        results.append(
            _edit_result(
                change.file_path,
                "Wrote file",
                success=True,
                snapshot_id=str(gen),
            )
        )

    result = OperationResult(
        success=True,
        status="committed",
        files=tuple(results),
        conflict_file=None,
        conflict_reason="",
    )
    finalize_ci_operation_result(
        context,
        result=result,
        changes=changes,
        edit_type="rename",
        description=description,
        ci_arbiter=getattr(svc, "arbiter", None),
    )
    return result


async def _perform_rename(
    *,
    svc: Any,
    context: ToolExecutionContext,
    resolved_path: str,
    line: int,
    character: int,
    new_name: str,
    dry_run: bool,
    extra_warnings: list[str] | None = None,
) -> ToolResult:
    """Shared body: build a SemanticRenamePlan and dispatch the operation commit."""
    try:
        planner = svc.rename_symbol_plan
        preview_planner = getattr(svc, "preview_rename_symbol_plan", None)
        if dry_run and callable(preview_planner):
            planner = preview_planner
        plan = planner(resolved_path, int(line), int(character), new_name)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("rename_symbol_plan raised for %s", resolved_path, exc_info=True)
        return ToolResult(output=f"LSP rename failed: {exc}", is_error=True)

    changes = getattr(plan, "changes", ()) or ()
    if not changes:
        return ToolResult(
            output=json.dumps(
                {
                    "status": "no_changes",
                    "new_name": new_name,
                    "files": [],
                    "warnings": list(extra_warnings or []),
                    "message": (
                        f"No rename changes produced for {resolved_path}:{line}. "
                        "Confirm the position points to a valid symbol and that "
                        "`new_name` is not already in use."
                    ),
                }
            ),
        )

    hard_errors: list[str] = []
    soft_warnings: list[str] = list(extra_warnings or [])
    for change in changes:
        path = change.file_path
        err = _team_repo_write_error(context, path, tool_name="ci_rename_symbol")
        if err is not None:
            hard_errors.append(err)
            continue
        warn = _team_repo_write_warning(context, path, tool_name="ci_rename_symbol")
        if warn is not None:
            soft_warnings.append(warn)
            record_coordination_warning(
                context, category="write_scope", message=warn,
            )
    if hard_errors:
        return ToolResult(
            output=(
                "Rename blocked by write-scope policy:\n  - "
                + "\n  - ".join(hard_errors)
            ),
            is_error=True,
        )

    if dry_run:
        file_summaries = [
            {
                "file_path": change.file_path,
                "status": "dry_run",
                "diff": _unified_diff(
                    change.base_content, change.final_content, change.file_path,
                ),
            }
            for change in changes
        ]
        return ToolResult(
            output=json.dumps(
                {
                    "status": "dry_run",
                    "new_name": new_name,
                    "files": file_summaries,
                    "warnings": soft_warnings,
                }
            ),
            metadata={"dry_run": True, "file_count": len(file_summaries)},
        )

    agent_id = str(
        context.metadata.get("agent_run_id")
        or context.metadata.get("agent_id")
        or "",
    )
    operation_changes = tuple(changes)
    description = f"rename to {new_name}"
    if context.metadata.get("daytona_sandbox") is not None or context.metadata.get("sandbox_id"):
        result = await _commit_rename_via_process(
            svc=svc,
            context=context,
            changes=operation_changes,
            agent_id=agent_id,
            description=description,
        )
    else:
        result = commit_ci_operation(
            context,
            operation_changes,
            agent_id=agent_id,
            edit_type="rename",
            description=description,
        )

    if result.success:
        files = [
            {"file_path": f.file_path, "status": "renamed"}
            for f in result.files
        ]
        return ToolResult(
            output=json.dumps(
                {
                    "status": "renamed",
                    "new_name": new_name,
                    "files": files,
                    "warnings": soft_warnings,
                    "message": None,
                }
            ),
            metadata={
                "file_count": len(files),
                "success_count": len(files),
            },
        )

    aborted = result.status.startswith("aborted")
    top_status = "aborted" if aborted else "failed"
    message = (
        f"Rename aborted ({result.status}): {result.conflict_reason}. "
        "Re-read the affected file(s) and retry."
        if aborted
        else f"Rename failed during commit: {result.conflict_reason}."
    )
    files_out = [
        {
            "file_path": f.file_path,
            "status": "failed",
            "message": f.message or result.conflict_reason,
        }
        for f in result.files
    ]
    return ToolResult(
        output=json.dumps(
            {
                "status": top_status,
                "new_name": new_name,
                "files": files_out,
                "warnings": soft_warnings,
                "message": message,
            }
        ),
        is_error=True,
        metadata={
            "file_count": len(files_out),
            "success_count": 0,
            "conflict_file": result.conflict_file,
            "conflict_reason": result.conflict_reason,
            "operation_status": result.status,
        },
    )


# -- Tool: ci_rename_symbol -------------------------------------------------


@tool(
    name="ci_rename_symbol",
    description=(
        "Rename a Python symbol by name across every file where it is referenced, "
        "using LSP semantics. Resolves `symbol` via the workspace symbol index, "
        "supports dotted names (`Foo.bar` narrows to the `bar` method on class "
        "`Foo`), and returns `status=\"ambiguous\"` with candidates when the "
        "name is not unique. Atomic: the whole rename commits or none of it does. "
        "Python-only for now."
    ),
    short_description="Rename a symbol by name across every referencing file (atomic).",
    input_model=CiRenameSymbolInput,
    output_model=CiRenameSymbolOutput,
)
async def ci_rename_symbol(
    symbol: str,
    new_name: str,
    kind: SymbolKind | None = None,
    file_hint: str | None = None,
    dry_run: bool = False,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Resolve *symbol* then apply an LSP-driven atomic rename."""
    svc = get_ci_service(context)
    if svc is None or not hasattr(svc, "rename_symbol_plan"):
        return ToolResult(output="LSP rename not available", is_error=True)
    invalid = _validate_new_name(new_name)
    if invalid is not None:
        return ToolResult(output=invalid, is_error=True)

    matches = _resolve_symbol(
        svc, symbol=symbol, kind=kind, file_hint=file_hint,
    )

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
        dry_run=dry_run,
    )
