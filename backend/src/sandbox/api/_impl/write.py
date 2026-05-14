"""Internal implementation for the public sandbox file-write verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api._impl._results import write_result_from_payload
from sandbox.api._impl._run_verb import _VerbSpec, _run_verb
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import WRITE_FILE_TIMEOUT_S
from sandbox.api.transport import DAEMON_OP_WRITE_FILE
from sandbox.models import WriteFileRequest, WriteFileResult


def _write_payload(request: WriteFileRequest) -> dict[str, object]:
    return {
        "path": request.path,
        "content": request.content,
        "actor_id": request.caller.agent_id,
        "caller": request.caller.audit_fields(),
        "description": request.default_description(f"write {request.path}"),
        "overwrite": request.overwrite,
    }


_SPEC = _VerbSpec(
    operation="write_file",
    daemon_op=DAEMON_OP_WRITE_FILE,
    timeout_s=WRITE_FILE_TIMEOUT_S,
    payload_builder=_write_payload,
    audit_payload_builder=lambda req: {"path": req.path},
    result_decoder=write_result_from_payload,
)


async def write_file(
    sandbox_id: str,
    request: WriteFileRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> WriteFileResult:
    """Write one UTF-8 file through sandbox-local OCC."""
    return await _run_verb(
        _SPEC, sandbox_id, request, audit_sink=audit_sink, transport=transport
    )


__all__ = ["write_file"]
