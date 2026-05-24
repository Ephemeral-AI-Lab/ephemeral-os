"""Client helpers for daemon request lifecycle RPCs."""

from __future__ import annotations

from collections.abc import Iterable

from sandbox.api.protocol import SandboxTransport
from sandbox.api.transport import (
    DAEMON_OP_CANCEL,
    DAEMON_OP_HEARTBEAT,
    DAEMON_OP_INFLIGHT_COUNT,
    DaemonSandboxTransport,
)

_CONTROL_TIMEOUT_S = 15


async def cancel(
    sandbox_id: str,
    request_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Cancel an in-flight daemon request by request id."""
    selected_transport = transport or DaemonSandboxTransport()
    return await selected_transport.call(
        sandbox_id,
        DAEMON_OP_CANCEL,
        {"request_id": request_id},
        timeout=_CONTROL_TIMEOUT_S,
    )


async def heartbeat(
    sandbox_id: str,
    request_ids: Iterable[str],
    *,
    engine_process_id: str = "",
    engine_started_at: float | None = None,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Refresh liveness for a batch of in-flight daemon request ids."""
    selected_transport = transport or DaemonSandboxTransport()
    payload: dict[str, object] = {
        "request_ids": [str(request_id) for request_id in request_ids if str(request_id)],
    }
    if engine_process_id:
        payload["engine_process_id"] = engine_process_id
    if engine_started_at is not None:
        payload["engine_started_at"] = float(engine_started_at)
    return await selected_transport.call(
        sandbox_id,
        DAEMON_OP_HEARTBEAT,
        payload,
        timeout=_CONTROL_TIMEOUT_S,
    )


async def inflight_count(
    sandbox_id: str,
    agent_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> int:
    """Return daemon-visible in-flight request count for one agent."""
    selected_transport = transport or DaemonSandboxTransport()
    response = await selected_transport.call(
        sandbox_id,
        DAEMON_OP_INFLIGHT_COUNT,
        {"agent_id": agent_id},
        timeout=_CONTROL_TIMEOUT_S,
    )
    return int(response.get("count") or 0)


__all__ = ["cancel", "heartbeat", "inflight_count"]
