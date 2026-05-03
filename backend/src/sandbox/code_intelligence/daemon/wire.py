"""Wire-format helpers for the transport-backed CI daemon backend."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sandbox.code_intelligence.core.types import (
    EditRequest,
    EditResult,
    EditSpec,
    OperationChange,
    OperationResult,
    WriteSpec,
)


def normalize_write_specs(
    specs: Sequence[WriteSpec] | WriteSpec,
) -> list[WriteSpec]:
    return [specs] if isinstance(specs, WriteSpec) else list(specs)


def normalize_edit_specs(
    specs: Sequence[EditSpec] | EditSpec,
) -> list[EditSpec]:
    return [specs] if isinstance(specs, EditSpec) else list(specs)


def writespec_to_dict(spec: WriteSpec) -> dict[str, Any]:
    return {
        "file_path": spec.file_path,
        "content": spec.content,
        "overwrite": spec.overwrite,
    }


def editspec_to_dict(spec: EditSpec) -> dict[str, Any]:
    return {
        "file_path": spec.file_path,
        "edits": list(spec.edits),
    }


def operation_change_to_dict(change: OperationChange) -> dict[str, Any]:
    return {
        "file_path": change.file_path,
        "base_content": change.base_content,
        "base_hash": change.base_hash,
        "final_content": change.final_content,
        "base_existed": change.base_existed,
        "strict_base": change.strict_base,
    }


def edit_request_to_dict(request: EditRequest) -> dict[str, Any]:
    return {
        "file_path": request.file_path,
        "old_text": request.old_text,
        "new_text": request.new_text,
        "agent_id": request.agent_id,
        "description": request.description,
    }


def edit_result_from_dict(d: dict[str, Any]) -> EditResult:
    return EditResult(
        success=bool(d.get("success", False)),
        file_path=str(d.get("file_path", "")),
        message=str(d.get("message", "")),
        conflict=bool(d.get("conflict", False)),
        conflict_reason=str(d.get("conflict_reason", "")),
        snapshot_id=str(d.get("snapshot_id", "")),
        timings=dict(d.get("timings") or {}),
    )


def operation_result_from_dict(d: dict[str, Any]) -> OperationResult:
    files = tuple(edit_result_from_dict(f) for f in (d.get("files") or ()))
    status = d.get("status", "failed")
    return OperationResult(
        success=bool(d.get("success", False)),
        status=status,  # type: ignore[arg-type]
        files=files,
        conflict_file=d.get("conflict_file"),
        conflict_reason=str(d.get("conflict_reason", "")),
        timings=dict(d.get("timings") or {}),
    )
