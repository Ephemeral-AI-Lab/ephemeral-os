"""Default transport for sandbox daemon API calls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandbox.host.daemon_client import call_daemon_api

DAEMON_PROTOCOL_VERSION = 1
DAEMON_PROTOCOL_FIELD = "_eos_daemon_protocol_version"


class DaemonSandboxTransport:
    """SandboxTransport implementation backed by the resident daemon."""

    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: Mapping[str, object],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        return await call_daemon_api(
            sandbox_id,
            op,
            versioned_payload(payload),
            timeout=timeout,
        )


def versioned_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Attach the client protocol version while preserving caller payloads."""
    return {
        DAEMON_PROTOCOL_FIELD: DAEMON_PROTOCOL_VERSION,
        **dict(payload),
    }


__all__ = [
    "DAEMON_PROTOCOL_FIELD",
    "DAEMON_PROTOCOL_VERSION",
    "DaemonSandboxTransport",
    "versioned_payload",
]
