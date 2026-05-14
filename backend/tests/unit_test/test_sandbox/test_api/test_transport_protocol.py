"""Tests for sandbox API transport/version contracts."""

from __future__ import annotations

from sandbox.api.protocol import SandboxTransport
from sandbox.api.transport import (
    DAEMON_PROTOCOL_FIELD,
    DAEMON_PROTOCOL_VERSION,
    versioned_payload,
)


class RecordingTransport:
    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: dict[str, object],
        *,
        timeout: int,
    ) -> dict[str, object]:
        del sandbox_id, op, payload, timeout
        return {}


def test_recording_transport_matches_protocol_shape() -> None:
    transport: SandboxTransport = RecordingTransport()
    assert transport is not None


def test_versioned_payload_attaches_daemon_protocol_version() -> None:
    assert versioned_payload({"path": "a.py"}) == {
        DAEMON_PROTOCOL_FIELD: DAEMON_PROTOCOL_VERSION,
        "path": "a.py",
    }
