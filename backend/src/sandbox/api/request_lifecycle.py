"""Client helpers for daemon invocation lifecycle RPCs."""

from __future__ import annotations

from collections.abc import Iterable

from sandbox.api.protocol import SandboxTransport
from sandbox.api.transport import (
    DAEMON_OP_INFLIGHT_COUNT,
    DAEMON_OP_INVOCATION_CANCEL,
    DAEMON_OP_INVOCATION_HEARTBEAT,
    DaemonSandboxTransport,
)

_CONTROL_TIMEOUT_S = 15


async def cancel(
    sandbox_id: str,
    invocation_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Cancel an in-flight daemon invocation by id."""
    selected_transport = transport or DaemonSandboxTransport()
    return await selected_transport.call(
        sandbox_id,
        DAEMON_OP_INVOCATION_CANCEL,
        {"invocation_id": invocation_id},
        timeout=_CONTROL_TIMEOUT_S,
    )


async def heartbeat(
    sandbox_id: str,
    invocation_ids: Iterable[str],
    *,
    engine_process_id: str = "",
    engine_started_at: float | None = None,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Refresh liveness for a batch of in-flight daemon invocation ids."""
    selected_transport = transport or DaemonSandboxTransport()
    ids = [str(invocation_id) for invocation_id in invocation_ids if str(invocation_id)]
    payload: dict[str, object] = {
        "invocation_ids": ids,
    }
    if engine_process_id:
        payload["engine_process_id"] = engine_process_id
    if engine_started_at is not None:
        payload["engine_started_at"] = float(engine_started_at)
    return await selected_transport.call(
        sandbox_id,
        DAEMON_OP_INVOCATION_HEARTBEAT,
        payload,
        timeout=_CONTROL_TIMEOUT_S,
    )


async def inflight_count(
    sandbox_id: str,
    agent_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> int:
    """Return daemon-visible in-flight invocation count for one agent."""
    selected_transport = transport or DaemonSandboxTransport()
    response = await selected_transport.call(
        sandbox_id,
        DAEMON_OP_INFLIGHT_COUNT,
        {"agent_id": agent_id},
        timeout=_CONTROL_TIMEOUT_S,
    )
    return int(response.get("count") or 0)


__all__ = ["cancel", "heartbeat", "inflight_count"]
