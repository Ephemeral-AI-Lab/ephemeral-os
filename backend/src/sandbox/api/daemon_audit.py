"""Public wrappers for the daemon audit pull / snapshot / reset_floor RPCs.

Trusted transport: AF_UNIX socket permissions are the authentication boundary.
See `docs/daemon-audit-pull-consolidation-v3/README.md#pull-rpc-trust-model`.
"""

from __future__ import annotations

from typing import Any

from sandbox.api.transport import (
    DAEMON_OP_AUDIT_PULL,
    DAEMON_OP_AUDIT_RESET_FLOOR,
    DAEMON_OP_AUDIT_SNAPSHOT,
    SandboxTransport,
    call_sandbox_daemon,
)

DEFAULT_PULL_LIMIT = 1000
DEFAULT_TIMEOUT_S = 5


async def audit_pull(
    sandbox_id: str,
    *,
    after_seq: int = -1,
    limit: int = DEFAULT_PULL_LIMIT,
    timeout: int = DEFAULT_TIMEOUT_S,
    transport: SandboxTransport | None = None,
) -> dict[str, Any]:
    """Pull audit events with seq > ``after_seq`` (exclusive)."""
    return await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_AUDIT_PULL,
        {"after_seq": after_seq, "limit": limit},
        timeout=timeout,
        transport=transport,
    )


async def audit_snapshot(
    sandbox_id: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    transport: SandboxTransport | None = None,
) -> dict[str, Any]:
    """O(1) snapshot of cached gauges; never walks the ring."""
    return await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_AUDIT_SNAPSHOT,
        {},
        timeout=timeout,
        transport=transport,
    )


async def audit_reset_floor(
    sandbox_id: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    transport: SandboxTransport | None = None,
) -> dict[str, Any]:
    """Operator escape hatch; gated by ``EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET``."""
    return await call_sandbox_daemon(
        sandbox_id,
        DAEMON_OP_AUDIT_RESET_FLOOR,
        {},
        timeout=timeout,
        transport=transport,
    )


__all__ = [
    "DEFAULT_PULL_LIMIT",
    "DEFAULT_TIMEOUT_S",
    "audit_pull",
    "audit_reset_floor",
    "audit_snapshot",
]
