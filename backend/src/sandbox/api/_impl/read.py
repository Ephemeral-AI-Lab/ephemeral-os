"""Internal implementation for the public sandbox file-read verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api._impl._results import read_result_from_payload
from sandbox.api._impl._run_verb import _VerbSpec, _run_verb
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import READ_FILE_TIMEOUT_S
from sandbox.api.transport import DAEMON_OP_READ_FILE
from sandbox.models import ReadFileRequest, ReadFileResult


_SPEC = _VerbSpec(
    operation="read_file",
    daemon_op=DAEMON_OP_READ_FILE,
    timeout_s=READ_FILE_TIMEOUT_S,
    payload_builder=lambda req: {
        "path": req.path,
        "caller": req.caller.audit_fields(),
    },
    audit_payload_builder=lambda req: {"path": req.path},
    result_decoder=read_result_from_payload,
)


async def read_file(
    sandbox_id: str,
    request: ReadFileRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> ReadFileResult:
    """Read one UTF-8 text file through the sandbox daemon."""
    return await _run_verb(
        _SPEC, sandbox_id, request, audit_sink=audit_sink, transport=transport
    )


__all__ = ["read_file"]
