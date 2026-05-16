"""Internal implementation for the public sandbox grep verb."""

from __future__ import annotations

from audit.base import AuditSink
from sandbox.api.tool.core.audit import audited_operation
from sandbox.api.tool.core.results import search_content_result_from_daemon_response
from sandbox.api.protocol import SandboxTransport
from sandbox.api.timeouts import SEARCH_CONTENT_TIMEOUT_S
from sandbox.api.transport import DAEMON_OP_SEARCH_CONTENT, DaemonSandboxTransport
from sandbox._shared.models import SearchContentRequest, SearchContentResult


async def search_content(
    sandbox_id: str,
    request: SearchContentRequest,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> SearchContentResult:
    """Regex-scan workspace file contents under the sandbox's leased snapshot."""
    selected_transport = transport or DaemonSandboxTransport()

    async def _call() -> SearchContentResult:
        payload: dict[str, object] = {
            "pattern": request.pattern,
            "output_mode": request.output_mode,
            "offset": request.offset,
            "case_insensitive": request.case_insensitive,
            "line_numbers": request.line_numbers,
            "multiline": request.multiline,
            "caller": request.caller.audit_fields(),
        }
        if request.path is not None:
            payload["path"] = request.path
        if request.glob_filter is not None:
            payload["glob_filter"] = request.glob_filter
        if request.head_limit is not None:
            payload["head_limit"] = request.head_limit
        raw = await selected_transport.call(
            sandbox_id,
            DAEMON_OP_SEARCH_CONTENT,
            payload,
            timeout=SEARCH_CONTENT_TIMEOUT_S,
        )
        return search_content_result_from_daemon_response(raw)

    return await audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation="search_content",
        caller=request.caller,
        payload={
            "pattern": request.pattern,
            "path": request.path or "",
            "output_mode": request.output_mode,
        },
        call=_call,
    )


__all__ = ["search_content"]
