"""Cross-file symbol rename tool backed by the code intelligence LSP."""

from __future__ import annotations

import difflib
import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import (
    abort_ci_write,
    finalize_ci_write,
    get_ci_service,
    prepare_ci_edit_intent,
    prepare_ci_write,
    release_ci_edit_intent,
)
from tools.core.decorator import tool
from tools.core.sandbox_runtime import resolve_daytona_path
from tools.daytona_toolkit._daytona_utils import (
    _team_repo_write_error,
    _team_repo_write_warning,
    record_coordination_warning,
)

logger = logging.getLogger(__name__)

_DIFF_MAX_CHARS = 8000
_IDENTIFIER_RE = r"^[A-Za-z_][A-Za-z0-9_]*$"


class CiRenameSymbolInput(BaseModel):
    file_path: str = Field(
        ...,
        description="File containing the symbol's definition or a reference.",
    )
    line: int = Field(..., ge=1, description="One-based line number of the symbol.")
    character: int = Field(
        default=0,
        ge=0,
        description=(
            "Zero-based column of the symbol. Pass 0 to auto-resolve to the first non-"
            "whitespace column (handles `def`/`class` lines correctly)."
        ),
    )
    new_name: str = Field(..., min_length=1, description="New identifier to rename to.")
    dry_run: bool = Field(
        default=False,
        description="Preview the per-file diffs without writing anything.",
    )


class FileRenameSummary(BaseModel):
    file_path: str = Field(..., description="Absolute path of the changed file.")
    status: str = Field(..., description="`renamed`, `dry_run`, or `failed`.")
    diff: str | None = Field(default=None, description="Unified diff for dry-run.")
    message: str | None = Field(default=None, description="Failure reason when status=failed.")


class CiRenameSymbolOutput(BaseModel):
    status: str = Field(..., description="`renamed`, `dry_run`, `no_changes`, or `failed`.")
    new_name: str = Field(..., description="Requested new identifier.")
    files: list[FileRenameSummary] = Field(
        default_factory=list,
        description="Per-file rename outcome (one entry per touched file).",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Advisory warnings (e.g., files outside write_scope).",
    )
    message: str | None = Field(default=None, description="Top-level status message.")


def _validate_new_name(new_name: str) -> str | None:
    import re

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


@tool(
    name="ci_rename_symbol",
    description=(
        "Rename a Python symbol (function, class, method, variable, or import binding) "
        "across every file where it is referenced, using LSP semantics. Prefer this "
        "over chained `daytona_edit_file` calls for cross-file renames — it will not "
        "hit unrelated string/comment matches and updates import sites atomically. "
        "Point it at any definition or reference of the symbol (give `file_path`, "
        "`line`, optional `character`). Use `dry_run=true` to preview per-file diffs "
        "before committing. Python-only for now."
    ),
    short_description="Rename a symbol across every file that references it.",
    input_model=CiRenameSymbolInput,
    output_model=CiRenameSymbolOutput,
)
async def ci_rename_symbol(
    file_path: str,
    line: int,
    new_name: str,
    character: int = 0,
    dry_run: bool = False,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Apply an LSP-driven symbol rename across all affected files."""
    svc = get_ci_service(context)
    if svc is None or not hasattr(svc, "rename_symbol"):
        return ToolResult(output="LSP rename not available", is_error=True)

    invalid = _validate_new_name(new_name)
    if invalid is not None:
        return ToolResult(output=invalid, is_error=True)

    resolved = resolve_daytona_path(file_path, context)
    try:
        changes = svc.rename_symbol(resolved, int(line), int(character), new_name)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("rename_symbol raised for %s", resolved, exc_info=True)
        return ToolResult(output=f"LSP rename failed: {exc}", is_error=True)

    if not isinstance(changes, dict) or not changes:
        return ToolResult(
            output=json.dumps(
                {
                    "status": "no_changes",
                    "new_name": new_name,
                    "files": [],
                    "warnings": [],
                    "message": (
                        f"No rename changes produced for {file_path}:{line}. "
                        "Confirm the position points to a valid symbol and that "
                        "`new_name` is not already in use."
                    ),
                }
            ),
        )

    # Hard-block check: if any target triggers a scope error, refuse the whole rename.
    hard_errors: list[str] = []
    soft_warnings: list[str] = []
    for changed_path in changes:
        err = _team_repo_write_error(context, changed_path, tool_name="ci_rename_symbol")
        if err is not None:
            hard_errors.append(err)
            continue
        warn = _team_repo_write_warning(context, changed_path, tool_name="ci_rename_symbol")
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
        # Dry-run stays serial: each slot does just one exec (read snapshot)
        # and Daytona's per-sandbox exec channel penalizes many tiny concurrent
        # requests more than the parallelism saves. Commit (read+write per
        # slot) has enough work per thread to benefit from parallelism.
        file_summaries: list[dict[str, Any]] = []
        for changed_path, new_content in changes.items():
            prepared, _scope_packet, _err = prepare_ci_write(
                context, changed_path, allow_scope_drift=True,
            )
            current = ""
            if prepared is not None:
                current = str(getattr(prepared, "current_content", "") or "")
                abort_ci_write(context, prepared)
            file_summaries.append(
                {
                    "file_path": changed_path,
                    "status": "dry_run",
                    "diff": _unified_diff(current, new_content, changed_path),
                }
            )
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

    # Commit phase: serial per file. An earlier experiment parallelized this
    # via asyncio.to_thread + gather, but measured slower end-to-end on live
    # Daytona sandboxes — each worker's `run_sync` spawns a fresh event loop
    # and HTTP session to the sandbox service, undoing connection warmth faster
    # than parallelism amortizes it. Each file still has its own OCC lock and
    # prepared token, so serial commits preserve the same per-file atomicity.
    # Failures are collected rather than aborting siblings: successful commits
    # are already atomic and safe to keep.
    committed: list[dict[str, Any]] = []
    for changed_path, new_content in changes.items():
        prepared, _scope_packet, err = prepare_ci_write(
            context, changed_path, allow_scope_drift=True,
        )
        if err is not None:
            committed.append(
                {"file_path": changed_path, "status": "failed", "message": err}
            )
            continue
        if prepared is None:
            committed.append(
                {
                    "file_path": changed_path,
                    "status": "failed",
                    "message": f"CI service unavailable for {changed_path}",
                }
            )
            continue
        intent_id = None
        try:
            prepared, intent_id = prepare_ci_edit_intent(
                context, prepared, content=new_content,
            )
            result = finalize_ci_write(
                context,
                prepared,
                content=new_content,
                edit_type="rename",
                description=f"rename to {new_name}",
            )
        finally:
            release_ci_edit_intent(context, intent_id)
            abort_ci_write(context, prepared)
        if getattr(result, "success", False):
            committed.append({"file_path": changed_path, "status": "renamed"})
        else:
            committed.append(
                {
                    "file_path": changed_path,
                    "status": "failed",
                    "message": str(
                        getattr(result, "message", "") or "commit failed"
                    ),
                }
            )

    any_failed = any(entry["status"] == "failed" for entry in committed)
    status = "failed" if any_failed else "renamed"
    message = None
    if any_failed:
        success_count = sum(1 for e in committed if e["status"] == "renamed")
        total = len(changes)
        message = (
            f"Rename partially applied: {success_count}/{total} files committed. "
            "Inspect the failed entries and re-run to finish the remaining files."
        )
    return ToolResult(
        output=json.dumps(
            {
                "status": status,
                "new_name": new_name,
                "files": committed,
                "warnings": soft_warnings,
                "message": message,
            }
        ),
        is_error=any_failed,
        metadata={
            "file_count": len(committed),
            "success_count": sum(1 for e in committed if e["status"] == "renamed"),
        },
    )
