"""Shared OperationResult builders for mutation planning failures."""

from __future__ import annotations

from sandbox.code_intelligence.core.types import EditResult, OperationResult


def error_result(
    file_path: str,
    message: str,
    *,
    conflict_reason: str,
    conflict_file: str | None = None,
) -> OperationResult:
    return OperationResult(
        success=False,
        status="failed",
        files=(EditResult(success=False, file_path=file_path, message=message),),
        conflict_file=conflict_file,
        conflict_reason=conflict_reason,
        timings={},
    )


def not_found_result(file_path: str) -> OperationResult:
    return error_result(
        file_path,
        f"Path does not exist: {file_path}",
        conflict_reason="not_found",
    )


def not_a_directory_result(file_path: str) -> OperationResult:
    return error_result(
        file_path,
        f"Path is not a directory: {file_path}",
        conflict_reason="not_a_directory",
        conflict_file=file_path,
    )


def identical_paths_result(file_path: str) -> OperationResult:
    return error_result(
        file_path,
        "src_path and dst_path are identical",
        conflict_reason="identical_paths",
    )


def dst_exists_result(dst_path: str) -> OperationResult:
    return error_result(
        dst_path,
        f"Destination exists: {dst_path} (pass overwrite=True to replace)",
        conflict_reason="dst_exists",
        conflict_file=dst_path,
    )


def patch_failed_result(file_path: str, errors: list[str]) -> OperationResult:
    return error_result(
        file_path,
        "; ".join(errors) if errors else "edit apply failed",
        conflict_reason="patch_failed",
        conflict_file=file_path,
    )
