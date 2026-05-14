"""Tests for sandbox API transport contracts."""

from __future__ import annotations

from sandbox.api.protocol import SandboxTransport
from sandbox.api.transport import (
    DAEMON_OP_EDIT_FILE,
    DAEMON_OP_READ_FILE,
    DAEMON_OP_SHELL,
    DAEMON_OP_WRITE_FILE,
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


def test_public_daemon_ops_use_api_v1_names() -> None:
    assert DAEMON_OP_READ_FILE == "api.v1.read_file"
    assert DAEMON_OP_WRITE_FILE == "api.v1.write_file"
    assert DAEMON_OP_EDIT_FILE == "api.v1.edit_file"
    assert DAEMON_OP_SHELL == "api.v1.shell"
