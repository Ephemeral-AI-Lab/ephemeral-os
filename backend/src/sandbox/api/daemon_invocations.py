"""Public wrappers for daemon invocation cancel, heartbeat, and count RPCs."""

from __future__ import annotations

from collections.abc import Iterable

from sandbox.api.transport import (
    DAEMON_OP_INFLIGHT_COUNT,
    DAEMON_OP_INVOCATION_CANCEL,
    DAEMON_OP_INVOCATION_HEARTBEAT,
    DAEMON_OP_ISOLATED_WORKSPACE_STATUS,
    DAEMON_OP_PTY_SESSION_COUNT,
    SandboxTransport,
    call_sandbox_daemon,
)

_CONTROL_TIMEOUT_S = 15


async def cancel(
    sandbox_id: str,
    invocation_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Cancel an in-flight daemon invocation by id."""
    return await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_INVOCATION_CANCEL,
        {"invocation_id": invocation_id},
        timeout=_CONTROL_TIMEOUT_S,
        transport=transport,
    )


async def heartbeat(
    sandbox_id: str,
    invocation_ids: Iterable[str],
    *,
    transport: SandboxTransport | None = None,
) -> dict[str, object]:
    """Refresh liveness for a batch of in-flight daemon invocation ids."""
    ids = [invocation_id for invocation_id in map(str, invocation_ids) if invocation_id]
    return await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_INVOCATION_HEARTBEAT,
        {"invocation_ids": ids},
        timeout=_CONTROL_TIMEOUT_S,
        transport=transport,
    )


async def inflight_count(
    sandbox_id: str,
    agent_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> int:
    """Return daemon-visible in-flight invocation count for one agent."""
    response = await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_INFLIGHT_COUNT,
        {"agent_id": agent_id},
        timeout=_CONTROL_TIMEOUT_S,
        transport=transport,
    )
    return int(response.get("count") or 0)


async def pty_session_count(
    sandbox_id: str,
    agent_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> int:
    """Return daemon-visible live PTY command session count for one agent."""
    response = await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_PTY_SESSION_COUNT,
        {"agent_id": agent_id},
        timeout=_CONTROL_TIMEOUT_S,
        transport=transport,
    )
    return int(response.get("count") or 0)


async def isolated_active(
    sandbox_id: str,
    agent_id: str,
    *,
    transport: SandboxTransport | None = None,
) -> bool:
    """Return True iff the agent has an open isolated workspace (daemon truth).

    Reads the authoritative ``get_handle`` verdict via the existing
    ``api.isolated_workspace.status`` op. The no-bootstrapped-pipeline error
    payload has no ``open`` key → treated as not isolated.
    """
    response = await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_ISOLATED_WORKSPACE_STATUS,
        {"agent_id": agent_id},
        timeout=_CONTROL_TIMEOUT_S,
        transport=transport,
    )
    return bool(response.get("open", False))


__all__ = [
    "cancel",
    "heartbeat",
    "inflight_count",
    "isolated_active",
    "pty_session_count",
]
