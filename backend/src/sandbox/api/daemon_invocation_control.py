"""Public wrappers for daemon invocation cancel, heartbeat, and count RPCs."""

from __future__ import annotations

from collections.abc import Iterable

from sandbox.api.transport import (
    DAEMON_OP_INFLIGHT_COUNT,
    DAEMON_OP_INVOCATION_CANCEL,
    DAEMON_OP_INVOCATION_HEARTBEAT,
    DaemonSandboxTransport,
    SandboxTransport,
)

_CONTROL_TIMEOUT_S = 15


async def cancel(
    sandbox_id: str,
    invocation_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Cancel an in-flight daemon invocation by id."""
    daemon_transport = transport or DaemonSandboxTransport()
    return await daemon_transport.call(
        sandbox_id,
        DAEMON_OP_INVOCATION_CANCEL,
        {"invocation_id": invocation_id},
        timeout=_CONTROL_TIMEOUT_S,
    )


async def heartbeat(
    sandbox_id: str,
    invocation_ids: Iterable[str],
    *,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Refresh liveness for a batch of in-flight daemon invocation ids."""
    daemon_transport = transport or DaemonSandboxTransport()
    ids = [invocation_id for invocation_id in map(str, invocation_ids) if invocation_id]
    return await daemon_transport.call(
        sandbox_id,
        DAEMON_OP_INVOCATION_HEARTBEAT,
        {"invocation_ids": ids},
        timeout=_CONTROL_TIMEOUT_S,
    )


async def inflight_count(
    sandbox_id: str,
    agent_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> int:
    """Return daemon-visible in-flight invocation count for one agent."""
    daemon_transport = transport or DaemonSandboxTransport()
    response = await daemon_transport.call(
        sandbox_id,
        DAEMON_OP_INFLIGHT_COUNT,
        {"agent_id": agent_id},
        timeout=_CONTROL_TIMEOUT_S,
    )
    return int(response.get("count") or 0)


__all__ = ["cancel", "heartbeat", "inflight_count"]
