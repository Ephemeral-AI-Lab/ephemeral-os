"""NDJSON parsing and SimpleNamespace result assembly for overlay commands."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from sandbox.code_intelligence.overlay.types import (
    OverlayChange,
    OverlayDiff,
    OverlayPolicyReject,
    OverlayRunError,
)


def parse_diff_ndjson(raw: str) -> OverlayDiff | OverlayPolicyReject:
    """Parse the ``diff.ndjson`` body produced by ``overlay_run.py``."""
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    if not lines:
        raise OverlayRunError("empty diff.ndjson payload")

    try:
        first = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise OverlayRunError(f"invalid diff.ndjson meta line: {exc}") from exc

    if isinstance(first, dict) and "_reject" in first:
        return _parse_reject(first["_reject"])
    if not (isinstance(first, dict) and "_meta" in first):
        raise OverlayRunError(
            f"diff.ndjson first line must be _meta or _reject: {first!r}"
        )
    meta = first["_meta"]
    if not isinstance(meta, dict):
        raise OverlayRunError(f"_meta block must be a dict, got {meta!r}")

    changes = [_parse_change(idx, line) for idx, line in enumerate(lines[1:], start=1)]
    raw_run_timings = meta.get("run_timings") or {}
    return OverlayDiff(
        exit_code=int(meta.get("exit_code") or 0),
        upper_bytes=int(meta.get("upper_bytes") or 0),
        upper_files=int(meta.get("upper_files") or 0),
        gitinclude_changes=tuple(changes),
        gitignore_paths=tuple(str(p) for p in meta.get("gitignore_paths") or ()),
        gitignore_truncated=bool(meta.get("gitignore_truncated")),
        direct_merged_bytes=int(meta.get("direct_merged_bytes") or 0),
        whiteouts_gitinclude=int(meta.get("whiteouts_gitinclude") or 0),
        whiteouts_gitignore_refused=int(
            meta.get("whiteouts_gitignore_refused") or 0
        ),
        dotgit_rejects=int(meta.get("dotgit_rejects") or 0),
        run_timings=parse_timing_dict(raw_run_timings),
        warnings=tuple(str(w) for w in meta.get("warnings") or ()),
    )


def _parse_reject(reject_meta: Any) -> OverlayPolicyReject:
    if not isinstance(reject_meta, dict):
        raise OverlayRunError(f"_reject block must be a dict, got {reject_meta!r}")
    raw_run_timings = reject_meta.get("run_timings") or {}
    return OverlayPolicyReject(
        reason=str(reject_meta.get("reason") or ""),
        paths=tuple(str(p) for p in reject_meta.get("paths") or ()),
        run_timings=parse_timing_dict(raw_run_timings),
    )


def _parse_change(idx: int, line: str) -> OverlayChange:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError as exc:
        raise OverlayRunError(f"invalid diff.ndjson entry at line {idx}: {exc}") from exc
    if not isinstance(entry, dict):
        raise OverlayRunError(
            f"diff.ndjson entry at line {idx} must be a dict: {entry!r}"
        )
    return OverlayChange(
        path=str(entry.get("path") or ""),
        kind=str(entry.get("kind") or "modify"),  # type: ignore[arg-type]
        base_content=str(entry.get("base_content") or ""),
        base_existed=bool(entry.get("base_existed")),
        final_content=(
            entry["final_content"] if entry.get("final_content") is not None else None
        ),
    )


def parse_timing_dict(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): round(float(value), 6)
        for key, value in raw.items()
        if isinstance(value, (int, float))
    }


def live_path(workspace_root: str, rel: str) -> str:
    rel = rel.replace("\\", "/").lstrip("/")
    return f"{workspace_root}/{rel}"


def audit_result(
    *,
    result_text: str,
    exit_code: int,
    gitinclude_committed: list[str],
    gitignore_merged: list[str],
    gitignore_merged_count: int,
    mixed_gitinclude_gitignore: bool,
    mixed_partial_apply: bool,
    ambient: list[str],
    git_commit_status: str | None,
    git_conflict_reason: str | None,
    git_conflict_file: str | None,
    warnings: list[str],
    overlay_run_timings: dict[str, float] | None = None,
) -> SimpleNamespace:
    """Preserve the downstream SimpleNamespace contract."""
    return SimpleNamespace(
        result=result_text,
        exit_code=exit_code,
        changed_paths=sorted(gitinclude_committed),
        ambient_changed_paths=sorted(ambient),
        files_written=len(gitinclude_committed),
        git_commit_status=git_commit_status,
        git_conflict_file=git_conflict_file,
        git_conflict_reason=git_conflict_reason,
        gitinclude_changed_paths=sorted(gitinclude_committed),
        gitignore_direct_merged_paths=sorted(gitignore_merged),
        gitignore_direct_merged_count=gitignore_merged_count,
        mixed_gitinclude_gitignore=mixed_gitinclude_gitignore,
        mixed_partial_apply=mixed_partial_apply,
        warnings=list(warnings),
        overlay_run_timings=dict(overlay_run_timings or {}),
    )


def reject_result(
    *,
    stdout: str,
    exit_code: int,
    reject: OverlayPolicyReject,
    overlay_run_timings: dict[str, float] | None = None,
) -> SimpleNamespace:
    detail = (
        f"{reject.reason}: {','.join(reject.paths)}"
        if reject.paths
        else reject.reason
    )
    return SimpleNamespace(
        result=stdout,
        exit_code=exit_code,
        changed_paths=[],
        ambient_changed_paths=[],
        files_written=0,
        git_commit_status="rejected",
        git_conflict_file=reject.paths[0] if reject.paths else None,
        git_conflict_reason=detail,
        gitinclude_changed_paths=[],
        gitignore_direct_merged_paths=[],
        gitignore_direct_merged_count=0,
        mixed_gitinclude_gitignore=False,
        mixed_partial_apply=False,
        warnings=[detail],
        overlay_run_timings=dict(overlay_run_timings or {}),
    )
