"""Public sandbox file-edit verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.audit.operation import (
    publish_operation_failed,
    publish_operation_result,
    publish_operation_started,
)
from sandbox.api.tool._payload import (
    caller_audit_fields,
    conflict_from_payload,
    error_message,
    int_from_payload,
    is_transient_transport_error,
    paths_from_payload,
    timings_from_payload,
)
from sandbox.api.timeouts import (
    EDIT_FILE_TIMEOUT_S,
    RECOVERY_READ_TIMEOUT_S,
    TRANSIENT_EDIT_ATTEMPTS,
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
        raw = await _call_edit_with_recovery(sandbox_id, request)
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


async def _call_edit_with_recovery(
    sandbox_id: str,
    request: EditFileRequest,
) -> dict[str, object]:
    payload = _edit_payload(request)
    last_exc: Exception | None = None
    for attempt_no in range(1, TRANSIENT_EDIT_ATTEMPTS + 1):
        try:
            return await call_daemon_api(
                sandbox_id,
                "api.edit_file",
                payload,
                timeout=EDIT_FILE_TIMEOUT_S,
            )
        except Exception as exc:
            if not is_transient_transport_error(exc):
                raise
            recovered = await _recover_if_edit_already_applied(
                sandbox_id,
                request,
                attempt_no=attempt_no,
            )
            if recovered is not None:
                return recovered
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _edit_payload(request: EditFileRequest) -> dict[str, object]:
    return {
        "path": request.path,
        "edits": [
            {"old_text": edit.old_text, "new_text": edit.new_text}
            for edit in request.edits
        ],
        "actor_id": request.caller.agent_id,
        "caller": caller_audit_fields(request.caller),
        "description": request.description or f"edit {request.path}",
    }


async def _recover_if_edit_already_applied(
    sandbox_id: str,
    request: EditFileRequest,
    *,
    attempt_no: int,
) -> dict[str, object] | None:
    try:
        raw = await call_daemon_api(
            sandbox_id,
            "api.read_file",
            {
                "path": request.path,
                "caller": caller_audit_fields(request.caller),
            },
            timeout=RECOVERY_READ_TIMEOUT_S,
        )
    except Exception:
        return None
    if not raw.get("success") or not raw.get("exists"):
        return None
    content = str(raw.get("content", ""))
    if not _edits_are_visible(content, request):
        return None
    return {
        "success": True,
        "changed_paths": [request.path],
        "applied_edits": len(request.edits),
        "status": "edited",
        "conflict": None,
        "conflict_reason": None,
        "timings": {
            "api.edit.recovered_after_transient": 1.0,
            "api.edit.recovery_attempt": float(attempt_no),
        },
    }


def _edits_are_visible(content: str, request: EditFileRequest) -> bool:
    return bool(request.edits) and all(edit.new_text in content for edit in request.edits)


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
    message = error_message(error)
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


def _is_edit_conflict(message: str) -> bool:
    lowered = message.lower()
    return (
        "anchor not found" in lowered
        or "anchor occurrence count mismatch" in lowered
        or "aborted_overlap" in lowered
        or "old_text_not_found" in lowered
    )


__all__ = ["edit_file"]
