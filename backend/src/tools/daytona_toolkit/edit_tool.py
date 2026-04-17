"""File editing tool for Daytona sandboxes."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from code_intelligence.editing.change_labels import change_actor_label
from code_intelligence.editing.patcher import Patcher, SearchReplaceEdit
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import (
    abort_ci_write,
    finalize_ci_write,
    get_ci_service,
    prepare_ci_edit_intent,
    prepare_ci_write,
    release_ci_edit_intent,
)
from tools.daytona_toolkit.tools import (
    _get_cwd,
    _path_error,
    _recover_sandbox,
    _require_sandbox,
    _resolve_path,
    _team_repo_write_error,
    _team_repo_write_warning,
    record_coordination_warning,
)
from tools.daytona_toolkit._daytona_utils import (
    _read_text_file_via_exec,
)
from tools.core.decorator import tool

logger = logging.getLogger(__name__)

_OUTPUT_MAX_CHARS = 8000


def _occ_required_error(tool_name: str, file_path: str) -> ToolResult:
    return ToolResult(
        output=(
            f"{tool_name}: Code intelligence/OCC is unavailable for edit of {file_path}. "
            "Direct sandbox write fallback is disabled."
        ),
        is_error=True,
        metadata={"occ_required": True},
    )


class DaytonaEditFileInput(BaseModel):
    file_path: str = Field(..., description="Path to the file to edit.")
    old_text: str = Field(
        default="",
        description="Exact text to find in single-edit mode. Pair only with new_text.",
    )
    new_text: str = Field(
        default="",
        description="Replacement text for single-edit mode. Do not send with edits.",
    )
    edits: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional batch of edit objects. Supported shape: "
            "{\"strategy\":\"search_replace\",\"search\":\"...\",\"replace\":\"...\"}."
        ),
    )
    description: str = Field(
        default="",
        description="Optional human-readable description of the edit.",
    )
    dry_run: bool = Field(
        default=False,
        description="Preview the edit and return a unified diff without applying changes.",
    )


class DaytonaEditFileOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    file_path: str = Field(..., description="Resolved file path that was edited.")
    status: str = Field(..., description="Edit result such as edited or dry_run.")
    occ: bool = Field(..., description="Whether optimistic concurrency control was used.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal edit warnings.")
    expected_hash: str | None = Field(
        default=None,
        description="Expected pre-edit content hash when OCC was used.",
    )
    timings: dict[str, Any] | None = Field(
        default=None,
        description="Optional edit timing metadata.",
    )
    diff: str | None = Field(
        default=None,
        description="Unified diff preview for dry-run edits.",
    )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _edit_success_result(
    *,
    context: ToolExecutionContext,
    file_path: str,
    warnings: list[str],
    patch_warnings: list[str],
    occ: bool,
    expected_hash: str = "",
    timings: dict[str, Any] | None = None,
) -> ToolResult:
    """Build a successful-edit ToolResult with consistent JSON output."""
    payload: dict[str, Any] = {
        "cwd": _get_cwd(context) or "",
        "file_path": file_path,
        "status": "edited",
        "occ": occ,
        "warnings": warnings + patch_warnings,
    }
    if occ and expected_hash:
        payload["expected_hash"] = expected_hash
    if timings:
        payload["timings"] = timings
    return ToolResult(
        output=json.dumps(payload),
        metadata={"file_path": file_path, "occ": occ, "timings": dict(timings or {})},
    )


def _scope_overlap_warning(
    context: ToolExecutionContext,
    file_path: str,
) -> str:
    """Check if other agents edited files in the same scope during this edit.

    Returns a warning string if another agent edited a file in the agent's scope,
    otherwise empty string. Call after a successful edit to alert the agent
    about potential concurrent changes in their scope.
    """
    arbiter = getattr(context, "metadata", {}).get("arbiter")
    if arbiter is None or not getattr(arbiter, "initialized", False):
        return ""

    agent_run_id = getattr(context, "metadata", {}).get("agent_run_id", "")
    write_scope: list[str] = getattr(context, "metadata", {}).get("write_scope", [])
    if not write_scope:
        return ""

    task_started_at = getattr(context, "metadata", {}).get("work_item_started_at", 0.0)
    if not task_started_at:
        return ""

    changes = arbiter.changes_since(
        task_started_at,
        team_run_id=str(getattr(context, "metadata", {}).get("team_run_id") or "") or None,
    )
    now = time.time()
    overlap_lines: list[str] = []
    for e in changes:
        if e.agent_run_id == agent_run_id:
            continue
        if not any(e.file_path.startswith(p.rstrip("/")) for p in write_scope):
            continue
        overlap_lines.append(
            f"  - {e.file_path} ({e.edit_type} by {change_actor_label(e)}, {int(now - e.created_at.timestamp())}s ago)"
        )

    if not overlap_lines:
        return ""

    return (
        f"\n[SCOPE OVERLAP WARNING] Other agents edited files in your scope "
        f"while you were editing {file_path}:\n" + "\n".join(overlap_lines)
    )


@tool(
    name="daytona_edit_file",
    description=(
        "Edit a file atomically. Use exactly one mode: "
        "(1) `old_text` + `new_text` for a single replacement or "
        "(2) `edits=[{\"strategy\":\"search_replace\",\"search\":\"...\",\"replace\":\"...\"}]` "
        "for batched replacements. Never send `new_text` together with `edits`."
    ),
    short_description="Apply atomic file edits.",
    input_model=DaytonaEditFileInput,
    output_model=DaytonaEditFileOutput,
)
async def daytona_edit_file(
    file_path: str,
    old_text: str = "",
    new_text: str = "",
    edits: list[dict[str, Any]] | None = None,
    description: str = "",
    dry_run: bool = False,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Edit a file in the Daytona sandbox atomically."""
    try:
        sandbox = await _require_sandbox(context)
    except Exception as exc:
        return ToolResult(output=str(exc), is_error=True)
    tool_started = time.perf_counter()
    tool_timings: dict[str, float] = {}

    file_path = _resolve_path(file_path, context)
    contract_error = _team_repo_write_error(context, file_path, tool_name="daytona_edit_file")
    if contract_error is not None:
        return ToolResult(output=contract_error, is_error=True)
    warnings: list[str] = []
    contract_warning = _team_repo_write_warning(context, file_path, tool_name="daytona_edit_file")
    if contract_warning is not None:
        warnings.append(contract_warning)
        record_coordination_warning(
            context,
            category="write_scope",
            message=contract_warning,
        )

    prepared = None
    intent_id = None
    current = ""
    current_hash = ""
    patcher = Patcher()
    normalized_edits, edit_error, legacy_not_found = _normalize_edits(
        old_text=old_text,
        new_text=new_text,
        edits=edits,
    )
    if edit_error is not None:
        return ToolResult(output=edit_error, is_error=True)
    svc = get_ci_service(context)
    refresh_prepared = getattr(svc, "refresh_prepared_write", None) if svc is not None else None
    refresh_supported = callable(refresh_prepared) and type(svc).__module__ != "unittest.mock"
    ci_write_supported = svc is not None and hasattr(svc, "prepare_write")
    if not ci_write_supported and not dry_run:
        return _occ_required_error("daytona_edit_file", file_path)

    if ci_write_supported:
        prepare_started = time.perf_counter()
        prepared, scope_packet, err = prepare_ci_write(
            context,
            file_path,
            allow_scope_drift=True,
        )
        tool_timings["prepare_ci_write"] = round(time.perf_counter() - prepare_started, 6)
        if err is not None:
            return ToolResult(
                output=err,
                is_error=True,
                metadata={"scope_packet": scope_packet, "conflict": True, "tool_timings": tool_timings},
            )
        if prepared is None:
            return ToolResult(
                output=(
                    f"daytona_edit_file: Code intelligence/OCC did not prepare edit of "
                    f"{file_path}. Direct sandbox write fallback is disabled."
                ),
                is_error=True,
                metadata={"occ_required": True},
            )
        if not bool(getattr(prepared, "existed", True)):
            abort_ci_write(context, prepared)
            return ToolResult(
                output=f"Path does not exist: {file_path}",
                is_error=True,
            )
        current = str(getattr(prepared, "current_content", "") or "")
        current_hash = str(getattr(prepared, "current_hash", "") or "")
    else:
        try:
            current, _ = await _read_text_file_via_exec(sandbox, file_path)
            current_hash = _content_hash(current)
        except Exception as exc:
            try:
                sandbox = await _recover_sandbox(context, exc)
                current, _ = await _read_text_file_via_exec(sandbox, file_path)
                current_hash = _content_hash(current)
            except Exception as recovery_exc:
                return ToolResult(
                    output=_path_error(recovery_exc, file_path)
                    or f"Cannot read file: {recovery_exc}",
                    is_error=True,
                )

    if prepared is not None and refresh_supported:
        refresh_started = time.perf_counter()
        refreshed = refresh_prepared(prepared)
        tool_timings["refresh_before_patch"] = round(time.perf_counter() - refresh_started, 6)
        if refreshed is not None:
            prepared = refreshed
            current = str(getattr(prepared, "current_content", "") or "")
            current_hash = str(getattr(prepared, "current_hash", "") or "")

    patch_started = time.perf_counter()
    patch_result = patcher.apply_edits(current, normalized_edits)
    tool_timings["patch_apply"] = round(time.perf_counter() - patch_started, 6)
    if not patch_result.success:
        abort_ci_write(context, prepared)
        return ToolResult(
            output=(
                f"Search text not found in {file_path}"
                if legacy_not_found and patch_result.errors == ["Edit 1: search text not found"]
                else "; ".join(patch_result.errors) or f"Edit failed for {file_path}"
            ),
            is_error=True,
        )

    new_content = patch_result.content

    if prepared is not None and refresh_supported:
        refresh_started = time.perf_counter()
        refreshed = refresh_prepared(prepared)
        tool_timings["refresh_before_commit"] = round(time.perf_counter() - refresh_started, 6)
        refreshed_content = str(getattr(refreshed, "current_content", "") or "")
        refreshed_hash = str(getattr(refreshed, "current_hash", "") or "")
        if refreshed_hash != current_hash or refreshed_content != current:
            prepared = refreshed
            current = refreshed_content
            current_hash = refreshed_hash
            patch_result = patcher.apply_edits(current, normalized_edits)
            if not patch_result.success:
                abort_ci_write(context, prepared)
                return ToolResult(
                    output=(
                        f"Requested edits no longer apply cleanly to {file_path} after a concurrent edit. "
                        "Re-read the file and retry."
                    ),
                    is_error=True,
                    metadata={"conflict": True},
                )
            new_content = patch_result.content

    if dry_run:
        # Show preview
        import difflib

        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        )
        diff_text = "".join(diff)
        if len(diff_text) > _OUTPUT_MAX_CHARS:
            diff_text = diff_text[:_OUTPUT_MAX_CHARS] + "\n... (truncated)"
        output = json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "file_path": file_path,
                "status": "dry_run",
                "occ": False,
                "diff": diff_text,
                "warnings": warnings + list(patch_result.warnings),
            }
        )
        abort_ci_write(context, prepared)
        return ToolResult(output=output, metadata={"dry_run": True})

    # Try OCC-coordinated edit via CI service
    if prepared is not None:
        try:
            prepared, intent_id = prepare_ci_edit_intent(context, prepared, content=new_content)
            finalize_started = time.perf_counter()
            result = finalize_ci_write(
                context,
                prepared,
                content=new_content,
                edit_type="edit",
                description=description,
            )
            tool_timings["finalize_ci_write"] = round(time.perf_counter() - finalize_started, 6)
        finally:
            release_ci_edit_intent(context, intent_id)
            abort_ci_write(context, prepared)
        if getattr(result, "success", False):
            scope_warning = _scope_overlap_warning(context, file_path)
            if scope_warning:
                warnings.append(scope_warning)
            tool_timings["tool_total"] = round(time.perf_counter() - tool_started, 6)
            timings = {
                "tool": tool_timings,
                "occ": getattr(result, "timings", {}),
            }
            return _edit_success_result(
                context=context,
                file_path=file_path,
                warnings=warnings,
                patch_warnings=list(patch_result.warnings),
                occ=True,
                expected_hash=current_hash,
                timings=timings,
            )
        return ToolResult(
            output=str(getattr(result, "message", "") or "Edit failed"),
            is_error=True,
            metadata={
                "conflict": bool(getattr(result, "conflict", False)),
                "timings": {
                    "tool": {**tool_timings, "tool_total": round(time.perf_counter() - tool_started, 6)},
                    "occ": getattr(result, "timings", {}),
                },
            },
        )
    return _occ_required_error("daytona_edit_file", file_path)


def _normalize_edits(
    *,
    old_text: str,
    new_text: str,
    edits: list[dict[str, Any]] | None,
) -> tuple[list[SearchReplaceEdit], str | None, bool]:
    """Validate and normalize tool inputs into patcher edit objects."""
    if edits is not None:
        if old_text or new_text:
            return [], "Provide either `old_text`/`new_text` or `edits`, not both.", False
        normalized: list[SearchReplaceEdit] = []
        for index, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                return [], f"Edit {index}: each edit must be an object.", False
            strategy = str(edit.get("strategy") or "").strip()

            # Auto-recover: LLMs sometimes omit strategy but pass recognizable keys
            if not strategy:
                if "old_text" in edit or "new_text" in edit or "old_string" in edit or "new_string" in edit:
                    strategy = "search_replace"
                elif "search" in edit or "replace" in edit:
                    strategy = "search_replace"

            if strategy == "search_replace":
                # Accept common LLM key variants: search/replace, old_text/new_text, old_string/new_string
                search = edit.get("search") or edit.get("old_text") or edit.get("old_string")
                replace = edit.get("replace") or edit.get("new_text") or edit.get("new_string")
                if not isinstance(search, str) or not isinstance(replace, str):
                    return (
                        [],
                        f"Edit {index}: search_replace requires string `search` and `replace`.",
                        False,
                    )
                normalized.append(SearchReplaceEdit(old_text=search, new_text=replace))
            else:
                return [], (
                    f"Edit {index}: unknown strategy '{strategy}'. "
                    "Use `{{\"strategy\": \"search_replace\", \"search\": \"...\", \"replace\": \"...\"}}` "
                    "or use top-level `old_text`/`new_text` for a single edit."
                ), False
        if not normalized:
            return [], "At least one edit is required.", False
        return normalized, None, False

    if not old_text:
        return [], (
            "Provide `old_text` (text to find) and `new_text` (replacement), "
            "or use `edits` with strategy `search_replace`."
        ), False
    return [SearchReplaceEdit(old_text=old_text, new_text=new_text)], None, True
