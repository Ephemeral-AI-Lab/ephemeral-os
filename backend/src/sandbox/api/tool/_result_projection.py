"""Project daemon response payloads into public sandbox API result models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from sandbox.api.tool._daemon_response_fields import (
    conflict_info_from_daemon_field,
    int_from_daemon_field,
    path_tuple_from_daemon_field,
    timing_map_from_daemon_field,
)
from sandbox._shared.models import (
    ConflictInfo,
    EditFileResult,
    GlobResult,
    GrepResult,
    GuardedResultBase,
    ReadFileResult,
    ShellResult,
)

TGuarded = TypeVar("TGuarded", bound=GuardedResultBase)


def read_result_from_daemon_response(response: Mapping[str, object]) -> ReadFileResult:
    return ReadFileResult(
        success=bool(response.get("success", False)),
        exists=bool(response.get("exists", False)),
        content=str(response.get("content", "")),
        encoding=str(response.get("encoding", "utf-8")),
        timings=timing_map_from_daemon_field(response.get("timings")),
    )


def glob_result_from_daemon_response(response: Mapping[str, object]) -> GlobResult:
    return GlobResult(
        success=bool(response.get("success", False)),
        filenames=path_tuple_from_daemon_field(response.get("filenames")),
        num_files=int_from_daemon_field(response.get("num_files"), default=0),
        truncated=bool(response.get("truncated", False)),
        timings=timing_map_from_daemon_field(response.get("timings")),
    )


def grep_result_from_daemon_response(
    response: Mapping[str, object],
) -> GrepResult:
    applied_limit_raw = response.get("applied_limit")
    applied_limit = (
        int_from_daemon_field(applied_limit_raw, default=0)
        if applied_limit_raw is not None
        else None
    )
    return GrepResult(
        success=bool(response.get("success", False)),
        output_mode=str(response.get("output_mode", "files_with_matches")),
        filenames=path_tuple_from_daemon_field(response.get("filenames")),
        content=str(response.get("content", "")),
        num_files=int_from_daemon_field(response.get("num_files"), default=0),
        num_lines=int_from_daemon_field(response.get("num_lines"), default=0),
        num_matches=int_from_daemon_field(response.get("num_matches"), default=0),
        applied_limit=applied_limit,
        applied_offset=int_from_daemon_field(response.get("applied_offset"), default=0),
        truncated=bool(response.get("truncated", False)),
        timings=timing_map_from_daemon_field(response.get("timings")),
    )


def guarded_result_from_daemon_response(
    result_cls: type[TGuarded],
    response: Mapping[str, object],
    *,
    timings: dict[str, float] | None = None,
    **extra: object,
) -> TGuarded:
    conflict = conflict_info_from_daemon_field(response.get("conflict"))
    error_payload = response.get("error")
    return result_cls(
        success=bool(response.get("success", False)),
        changed_paths=path_tuple_from_daemon_field(response.get("changed_paths")),
        changed_path_kinds=_changed_path_kinds_from_daemon_field(
            response.get("changed_path_kinds")
        ),
        mutation_source=str(response.get("mutation_source") or ""),
        status=str(response.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(response.get("conflict_reason"))
            if response.get("conflict_reason") is not None
            else None
        ),
        error=dict(error_payload) if isinstance(error_payload, dict) else None,
        timings=(
            timings
            if timings is not None
            else timing_map_from_daemon_field(response.get("timings"))
        ),
        **cast(Any, extra),
    )


def _changed_path_kinds_from_daemon_field(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(path): str(kind)
        for path, kind in raw.items()
        if str(path or "").strip() and str(kind or "").strip()
    }


def shell_result_from_daemon_response(
    response: Mapping[str, object],
    *,
    timings: dict[str, float],
) -> ShellResult:
    return guarded_result_from_daemon_response(
        ShellResult,
        response,
        exit_code=int_from_daemon_field(response.get("exit_code"), default=1),
        stdout=str(response.get("stdout", "")),
        stderr=str(response.get("stderr", "")),
        warnings=path_tuple_from_daemon_field(response.get("warnings")),
        timings=timings,
    )


def edit_conflict_result(path: str, message: str) -> EditFileResult:
    return EditFileResult(
        success=False,
        changed_paths=(path,),
        applied_edits=0,
        status="aborted_overlap",
        conflict=ConflictInfo.overlap(path=path, message=message),
        conflict_reason=message,
        timings={},
    )


def shell_conflict_result(
    message: str,
    *,
    timings: dict[str, float],
) -> ShellResult:
    return ShellResult(
        success=False,
        exit_code=1,
        stdout="",
        stderr="",
        changed_paths=(),
        status="rejected",
        conflict=ConflictInfo.rejected(message=message),
        conflict_reason=message,
        warnings=(),
        timings=timings,
    )


def shell_error_result(
    *,
    reason: str,
    message: str,
    timings: dict[str, float] | None = None,
) -> ShellResult:
    return ShellResult(
        success=False,
        exit_code=1,
        stdout="",
        stderr="",
        changed_paths=(),
        status="error",
        conflict=ConflictInfo.rejected(reason=reason, message=message),
        conflict_reason=message,
        warnings=(),
        timings=timings or {},
    )
