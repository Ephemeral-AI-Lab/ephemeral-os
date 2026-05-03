"""Unit tests for the Phase 2 CI RPC client and launcher retry path."""

from __future__ import annotations

import base64
import re
import struct
from typing import Any

import msgpack
import pytest

from sandbox.code_intelligence.in_sandbox.ci_protocol import (
    CI_PROTOCOL_VERSION,
    encode_frame,
)
from sandbox.code_intelligence.rpc.client import (
    CiDaemonRpcError,
    CiDaemonUnavailable,
    CiRpcClient,
)
from sandbox.code_intelligence.rpc.launcher import bundle_hash


class _FakeTransport:
    name = "fake"

    def __init__(
        self,
        *,
        fail_shim_attempts: int = 0,
        response_error: dict[str, Any] | None = None,
    ) -> None:
        self.exec_calls: list[str] = []
        self.fail_shim_attempts = fail_shim_attempts
        self.response_error = response_error
        self.spawn_count = 0
        self.alive = False
        self.socket_ready = False

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> Any:
        del sandbox_id, cwd, timeout
        self.exec_calls.append(command)
        if 'printf %s "$HOME"' in command:
            return _result(0, "/home/u")
        if "test -f" in command and "daemon.pid" in command and "kill -0" in command:
            return _result(0 if self.alive else 1, "")
        if ".bundle-hash" in command and "tar -xzf" not in command:
            return _result(0, bundle_hash() + "\n")
        if "setsid nohup python3 -m sandbox.code_intelligence.in_sandbox" in command:
            self.spawn_count += 1
            self.alive = True
            self.socket_ready = True
            return _result(0, "1234\n")
        if command.startswith("test -S"):
            return _result(0 if self.socket_ready else 1, "")
        if "socket.socket(socket.AF_UNIX)" in command:
            if self.fail_shim_attempts:
                self.fail_shim_attempts -= 1
                return _result(1, "connect failed")
            request = _extract_request(command)
            response: dict[str, Any]
            if self.response_error is not None:
                response = {
                    "v": CI_PROTOCOL_VERSION,
                    "id": request["id"],
                    "ok": False,
                    "error": self.response_error,
                }
            else:
                response = {
                    "v": CI_PROTOCOL_VERSION,
                    "id": request["id"],
                    "ok": True,
                    "result": {"pong": True, "op": request["op"]},
                }
            return _result(0, base64.b64encode(encode_frame(response)).decode("ascii"))
        return _result(0, "")


def _result(exit_code: int, stdout: str) -> Any:
    return type("R", (), {"exit_code": exit_code, "stdout": stdout})()


def _extract_request(command: str) -> dict[str, Any]:
    match = re.search(r"base64\.b64decode\('([^']+)'\)", command)
    assert match, command
    frame = base64.b64decode(match.group(1))
    (length,) = struct.unpack(">I", frame[:4])
    return msgpack.unpackb(frame[4 : 4 + length], raw=False)


@pytest.mark.asyncio
async def test_call_returns_success_result() -> None:
    transport = _FakeTransport()
    transport.alive = True
    transport.socket_ready = True
    client = CiRpcClient(transport, "sb-1", "/ws")  # type: ignore[arg-type]

    assert await client.call("ping") == {"pong": True, "op": "ping"}
    assert transport.spawn_count == 0


@pytest.mark.asyncio
async def test_connection_failure_ensures_daemon_then_retries() -> None:
    transport = _FakeTransport(fail_shim_attempts=1)
    client = CiRpcClient(transport, "sb-1", "/ws")  # type: ignore[arg-type]

    assert await client.call("ping") == {"pong": True, "op": "ping"}
    assert transport.spawn_count == 1


@pytest.mark.asyncio
async def test_second_connection_failure_raises_unavailable() -> None:
    transport = _FakeTransport(fail_shim_attempts=2)
    client = CiRpcClient(transport, "sb-1", "/ws")  # type: ignore[arg-type]

    with pytest.raises(CiDaemonUnavailable, match="daemon unreachable after respawn"):
        await client.call("ping")
    assert transport.spawn_count == 1


@pytest.mark.asyncio
async def test_error_envelope_raises_typed_rpc_error() -> None:
    transport = _FakeTransport(
        response_error={
            "kind": "UnsupportedOp",
            "message": "unknown op: nope",
            "details": {"op": "nope"},
        }
    )
    transport.alive = True
    transport.socket_ready = True
    client = CiRpcClient(transport, "sb-1", "/ws")  # type: ignore[arg-type]

    with pytest.raises(CiDaemonRpcError) as exc:
        await client.call("nope")
    assert exc.value.kind == "UnsupportedOp"
    assert exc.value.details == {"op": "nope"}

