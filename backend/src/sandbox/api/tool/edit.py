"""Internal implementation for the public sandbox file-edit verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api.tool.core.audit import audited_operation
from sandbox.api.tool.core.conflicts import is_edit_conflict
from sandbox.api.tool.core.daemon_response import (
    error_message,
    int_from_daemon_response,
)
from sandbox.api.tool.core.results import (
    edit_conflict_result,
    guarded_result_from_daemon_response,
)
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import EDIT_FILE_TIMEOUT_S
from sandbox.api.transport import DAEMON_OP_EDIT_FILE, DaemonSandboxTransport
from sandbox._shared.models import EditFileRequest, EditFileResult


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
        payload: dict[str, object] = {
            "agent_id": request.caller.agent_id,
            "path": request.path,
            "edits": [
                {"old_text": edit.old_text, "new_text": edit.new_text}
                for edit in request.edits
            ],
            "caller": request.caller.audit_fields(),
            "description": request.default_description(f"edit {request.path}"),
        }
        if request.invocation_id:
            payload["invocation_id"] = request.invocation_id
        raw = await selected_transport.call(
            sandbox_id,
            DAEMON_OP_EDIT_FILE,
            payload,
            timeout=EDIT_FILE_TIMEOUT_S,
        )
        return guarded_result_from_daemon_response(
            EditFileResult,
            raw,
            applied_edits=int_from_daemon_response(raw.get("applied_edits"), default=0),
        )

    def _conflict_from_error(exc: BaseException) -> EditFileResult | None:
        if not is_edit_conflict(exc):
            return None
        return edit_conflict_result(request.path, error_message(exc))

    return await audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation="edit_file",
        caller=request.caller,
        payload={"path": request.path},
        call=_call,
        conflict_from_error=_conflict_from_error,
    )


__all__ = ["edit_file"]
