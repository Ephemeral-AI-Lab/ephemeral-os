"""Internal implementation for the public sandbox file-write verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api._impl._audit import audited_operation
from sandbox.api._impl._payload import caller_audit_fields
from sandbox.api._impl._results import write_result_from_payload
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import WRITE_FILE_TIMEOUT_S
from sandbox.api.transport import DaemonSandboxTransport
from sandbox.models import WriteFileRequest, WriteFileResult


async def write_file(
    sandbox_id: str,
    request: WriteFileRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> WriteFileResult:
    """Write one UTF-8 file through sandbox-local OCC."""
    selected_transport = transport or DaemonSandboxTransport()

    async def _call() -> WriteFileResult:
        raw = await selected_transport.call(
            sandbox_id,
            "api.write_file",
            _write_payload(request),
            timeout=WRITE_FILE_TIMEOUT_S,
        )
        return write_result_from_payload(raw)

    return await audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation="write_file",
        caller=request.caller,
        payload={"path": request.path},
        call=_call,
    )


def _write_payload(request: WriteFileRequest) -> dict[str, object]:
    return {
        "path": request.path,
        "content": request.content,
        "actor_id": request.caller.agent_id,
        "caller": caller_audit_fields(request.caller),
        "description": request.description or f"write {request.path}",
        "overwrite": request.overwrite,
    }


__all__ = ["write_file"]
