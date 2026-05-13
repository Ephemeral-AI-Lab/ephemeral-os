"""Public sandbox file-edit verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.audit.operation import (
    publish_operation_failed,
    publish_operation_result,
    publish_operation_started,
)
from sandbox.api.tool._payload import (
    caller_envelope,
    conflict_from_payload,
    int_from_payload,
    paths_from_payload,
    timings_from_payload,
)
from sandbox.models import ConflictInfo, EditFileRequest, EditFileResult
from sandbox.host.daemon_client import call_daemon_api


async def edit_file(
    sandbox_id: str,
    request: EditFileRequest,
    *,
    audit_sink: AuditSink | None = None,
) -> EditFileResult:
    """Apply search/replace edits through sandbox-local OCC."""
    publish_operation_started(
        audit_sink,
        sandbox_id=sandbox_id,
        operation="edit_file",
        caller=request.caller,
        payload={"path": request.path},
    )
    try:
        raw = await call_daemon_api(
            sandbox_id,
            "api.edit_file",
            {
                "path": request.path,
                "edits": [
                    {"old_text": edit.old_text, "new_text": edit.new_text}
                    for edit in request.edits
                ],
                "actor_id": request.caller.agent_id,
                "caller": caller_envelope(request.caller),
                "description": request.description or f"edit {request.path}",
            },
            timeout=60,
        )
        result = _result_from_payload(raw)
    except Exception as exc:
        conflict_result = _conflict_result_from_error(request.path, exc)
        if conflict_result is not None:
            publish_operation_result(
                audit_sink,
                sandbox_id=sandbox_id,
                operation="edit_file",
                caller=request.caller,
                result=conflict_result,
            )
            return conflict_result
        publish_operation_failed(
            audit_sink,
            sandbox_id=sandbox_id,
            operation="edit_file",
            caller=request.caller,
            error=exc,
        )
        raise
    publish_operation_result(
        audit_sink,
        sandbox_id=sandbox_id,
        operation="edit_file",
        caller=request.caller,
        result=result,
    )
    return result


def _result_from_payload(raw: dict[str, object]) -> EditFileResult:
    conflict = conflict_from_payload(raw.get("conflict"))
    return EditFileResult(
        success=bool(raw.get("success", False)),
        changed_paths=paths_from_payload(raw.get("changed_paths")),
        applied_edits=int_from_payload(raw.get("applied_edits"), default=0),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        timings=timings_from_payload(raw.get("timings")),
    )


def _conflict_result_from_error(path: str, error: BaseException) -> EditFileResult | None:
    message = _error_message(error)
    if not _is_edit_conflict(message):
        return None
    return EditFileResult(
        success=False,
        changed_paths=(path,),
        applied_edits=0,
        status="aborted_overlap",
        conflict=ConflictInfo(
            reason="aborted_overlap",
            conflict_file=path,
            message=message,
        ),
        conflict_reason=message,
        timings={},
    )


def _error_message(error: BaseException) -> str:
    message = str(getattr(error, "message", "") or error)
    if message.startswith("internal_error: "):
        return message.removeprefix("internal_error: ")
    return message


def _is_edit_conflict(message: str) -> bool:
    lowered = message.lower()
    return (
        "anchor not found" in lowered
        or "anchor occurrence count mismatch" in lowered
        or "aborted_overlap" in lowered
        or "old_text_not_found" in lowered
    )


__all__ = ["edit_file"]
