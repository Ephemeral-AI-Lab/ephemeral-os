"""Build shared daemon payload fields for public sandbox tool operations."""

from __future__ import annotations

from sandbox._shared.models import SandboxRequestBase


def daemon_request_identity(request: SandboxRequestBase) -> dict[str, object]:
    payload: dict[str, object] = {
        "agent_id": request.caller.agent_id,
        "caller": request.caller.audit_fields(),
    }
    if request.invocation_id:
        payload["invocation_id"] = request.invocation_id
    return payload
