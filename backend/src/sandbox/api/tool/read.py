"""Internal implementation for the public sandbox file-read verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api.tool.core.audit import audited_operation
from sandbox.api.tool.core.results import read_result_from_daemon_response
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import READ_FILE_TIMEOUT_S
from sandbox.api.transport import DAEMON_OP_READ_FILE, DaemonSandboxTransport
from sandbox._shared.models import ReadFileRequest, ReadFileResult


async def read_file(
    sandbox_id: str,
    request: ReadFileRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> ReadFileResult:
    """Read one UTF-8 text file through the sandbox daemon."""
    selected_transport = transport or DaemonSandboxTransport()

    async def _call() -> ReadFileResult:
        payload: dict[str, object] = {
            "agent_id": request.caller.agent_id,
            "path": request.path,
            "caller": request.caller.audit_fields(),
        }
        if request.invocation_id:
            payload["invocation_id"] = request.invocation_id
        raw = await selected_transport.call(
            sandbox_id,
            DAEMON_OP_READ_FILE,
            payload,
            timeout=READ_FILE_TIMEOUT_S,
        )
        return read_result_from_daemon_response(raw)

    return await audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation="read_file",
        caller=request.caller,
        payload={"path": request.path},
        call=_call,
    )


__all__ = ["read_file"]
