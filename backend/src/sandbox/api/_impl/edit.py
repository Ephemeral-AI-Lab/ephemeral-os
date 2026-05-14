"""Internal implementation for the public sandbox file-edit verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api._impl._classifiers import is_edit_conflict
from sandbox.api._impl._payload import error_message
from sandbox.api._impl._results import edit_conflict_result, edit_result_from_payload
from sandbox.api._impl._run_verb import _VerbSpec, _run_verb
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import EDIT_FILE_TIMEOUT_S
from sandbox.api.transport import DAEMON_OP_EDIT_FILE
from sandbox.models import EditFileRequest, EditFileResult


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


def _conflict_from_error(
    request: EditFileRequest, error: BaseException
) -> EditFileResult | None:
    if not is_edit_conflict(error):
        return None
    return edit_conflict_result(request.path, error_message(error))


_SPEC = _VerbSpec(
    operation="edit_file",
    daemon_op=DAEMON_OP_EDIT_FILE,
    timeout_s=EDIT_FILE_TIMEOUT_S,
    payload_builder=_edit_payload,
    audit_payload_builder=lambda req: {"path": req.path},
    result_decoder=edit_result_from_payload,
    conflict_from_error=_conflict_from_error,
)


async def edit_file(
    sandbox_id: str,
    request: EditFileRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> EditFileResult:
    """Apply search/replace edits through sandbox-local OCC."""
    return await _run_verb(
        _SPEC, sandbox_id, request, audit_sink=audit_sink, transport=transport
    )


__all__ = ["edit_file"]
