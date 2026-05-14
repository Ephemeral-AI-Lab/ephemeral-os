"""Internal implementation for the public sandbox file-edit verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api._impl._audit import audited_operation
from sandbox.api._impl._classifiers import is_edit_conflict
from sandbox.api._impl._payload import error_message
from sandbox.api._impl._results import edit_conflict_result, edit_result_from_payload
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import EDIT_FILE_TIMEOUT_S
from sandbox.api.transport import (
    DAEMON_OP_EDIT_FILE,
    DaemonSandboxTransport,
)
from sandbox.models import EditFileRequest, EditFileResult


async def edit_file(
    sandbox_id: str,
    request: EditFileRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> EditFileResult:
    """Apply search/replace edits through sandbox-local OCC."""
    selected_transport = transport or DaemonSandboxTransport()

    async def _call() -> EditFileResult:
        raw = await selected_transport.call(
            sandbox_id,
            DAEMON_OP_EDIT_FILE,
            _edit_payload(request),
            timeout=EDIT_FILE_TIMEOUT_S,
        )
        return edit_result_from_payload(raw)

    return await audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation="edit_file",
        caller=request.caller,
        payload={"path": request.path},
        call=_call,
        conflict_from_error=lambda exc: _conflict_result_from_error(request.path, exc),
    )


def _edit_payload(request: EditFileRequest) -> dict[str, object]:
    return {
        "path": request.path,
        "edits": [
            {"old_text": edit.old_text, "new_text": edit.new_text}
            for edit in request.edits
        ],
        "actor_id": request.caller.agent_id,
        "caller": request.caller.audit_fields(),
        "description": request.default_description(f"edit {request.path}"),
    }


def _conflict_result_from_error(path: str, error: BaseException) -> EditFileResult | None:
    if not is_edit_conflict(error):
        return None
    return edit_conflict_result(path, error_message(error))


__all__ = ["edit_file"]
