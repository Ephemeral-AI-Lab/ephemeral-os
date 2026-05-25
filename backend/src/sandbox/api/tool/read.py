"""Internal implementation for the public sandbox file-read verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api.tool._daemon_payload import daemon_request_identity
from sandbox.api.tool._operation_audit import run_audited_operation
from sandbox.api.tool._result_projection import read_result_from_daemon_response
from sandbox.api.timeouts import READ_FILE_TIMEOUT_S
from sandbox.api.transport import DAEMON_OP_READ_FILE, DaemonSandboxTransport, SandboxTransport
from sandbox._shared.models import ReadFileRequest, ReadFileResult


async def read_file(
    sandbox_id: str,
    request: ReadFileRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> ReadFileResult:
    """Read one UTF-8 text file through the sandbox daemon."""
    daemon_transport = transport or DaemonSandboxTransport()

    async def _call() -> ReadFileResult:
        payload = daemon_request_identity(request) | {"path": request.path}
        response = await daemon_transport.call(
            sandbox_id,
            DAEMON_OP_READ_FILE,
            payload,
            timeout=READ_FILE_TIMEOUT_S,
        )
        return read_result_from_daemon_response(response)

    return await run_audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation="read_file",
        caller=request.caller,
        payload={"path": request.path},
        call=_call,
    )


__all__ = ["read_file"]
