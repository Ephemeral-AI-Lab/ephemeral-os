"""Internal implementation for the public sandbox shell verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api.tool._conflict_detection import is_shell_conflict
from sandbox.api.tool._daemon_payload import daemon_request_identity
from sandbox.api.tool._daemon_response_fields import (
    timing_map_from_daemon_field,
    user_visible_error_message,
)
from sandbox.api.tool._operation_audit import run_audited_operation
from sandbox.api.tool._result_projection import (
    shell_conflict_result,
    shell_error_result,
    shell_result_from_daemon_response,
)
from sandbox.api.timeouts import shell_dispatch_timeout
from sandbox.api.transport import DAEMON_OP_SHELL, DaemonSandboxTransport, SandboxTransport
from sandbox._shared.clock import monotonic_now
from sandbox._shared.models import ShellRequest, ShellResult


async def shell(
    sandbox_id: str,
    request: ShellRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> ShellResult:
    """Run a shell command through sandbox-local overlay and OCC."""
    total_start = monotonic_now()
    cwd = (request.cwd or "").strip() or "."
    daemon_transport = transport or DaemonSandboxTransport()

    async def _call() -> ShellResult:
        if request.stdin is not None:
            return shell_error_result(
                reason="stdin_not_supported",
                message="snapshot overlay shell does not accept stdin",
                timings={"api.shell.total_s": monotonic_now() - total_start},
            )
        payload = daemon_request_identity(request) | {
            "command": request.command,
            "cwd": cwd,
            "timeout_seconds": request.timeout,
            "description": request.default_description("shell"),
        }
        if request.background:
            payload["background"] = True
        response = await daemon_transport.call(
            sandbox_id,
            DAEMON_OP_SHELL,
            payload,
            timeout=shell_dispatch_timeout(request.timeout),
        )
        timings = timing_map_from_daemon_field(response.get("timings"))
        timings["api.shell.dispatch_total_s"] = monotonic_now() - total_start
        return shell_result_from_daemon_response(response, timings=timings)

    def _conflict_from_error(exc: BaseException) -> ShellResult | None:
        if not is_shell_conflict(exc):
            return None
        return shell_conflict_result(
            user_visible_error_message(exc),
            timings={"api.shell.dispatch_total_s": monotonic_now() - total_start},
        )

    return await run_audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation="shell",
        caller=request.caller,
        payload={"cwd": cwd},
        call=_call,
        conflict_from_error=_conflict_from_error,
    )


__all__ = ["shell"]
