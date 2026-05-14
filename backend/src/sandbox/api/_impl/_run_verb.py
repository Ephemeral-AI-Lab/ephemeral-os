"""Shared dispatch helper for sandbox file verbs (read/write/edit)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from audit.base import AuditSink, JsonValue
from sandbox.api._impl._audit import audited_operation
from sandbox.api.protocol import SandboxTransport
from sandbox.api.transport import DaemonSandboxTransport
from sandbox.audit.translation import SandboxOperation
from sandbox.models import SandboxResultBase


@dataclass(frozen=True)
class _VerbSpec:
    """Per-verb wiring consumed by :func:`_run_verb`."""

    operation: SandboxOperation
    daemon_op: str
    timeout_s: float
    payload_builder: Callable[[Any], dict[str, object]]
    audit_payload_builder: Callable[[Any], Mapping[str, JsonValue]]
    result_decoder: Callable[[dict[str, object]], SandboxResultBase]
    conflict_from_error: Callable[[Any, BaseException], SandboxResultBase | None] | None = None


async def _run_verb(
    spec: _VerbSpec,
    sandbox_id: str,
    request: Any,
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
) -> Any:
    """Dispatch one verb through the daemon transport with audit guards."""
    selected_transport = transport or DaemonSandboxTransport()

    async def _call() -> Any:
        raw = await selected_transport.call(
            sandbox_id,
            spec.daemon_op,
            spec.payload_builder(request),
            timeout=spec.timeout_s,
        )
        return spec.result_decoder(raw)

    conflict_handler: Callable[[BaseException], Any] | None = None
    if spec.conflict_from_error is not None:
        verb_conflict = spec.conflict_from_error

        def conflict_handler(exc: BaseException) -> Any:
            return verb_conflict(request, exc)

    return await audited_operation(
        audit_sink=audit_sink,
        sandbox_id=sandbox_id,
        operation=spec.operation,
        caller=request.caller,
        payload=spec.audit_payload_builder(request),
        call=_call,
        conflict_from_error=conflict_handler,
    )


__all__ = ["_VerbSpec", "_run_verb"]
