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


# ---------------------------------------------------------------------------
# Phase 5 — native ci_rpc verb preference + EOS_CI_FORCE_SHIM fallback
# ---------------------------------------------------------------------------


class _VerbTransport(_FakeTransport):
    """FakeTransport extended with a recordable native ci_rpc verb."""

    def __init__(self, *, raise_not_implemented: bool = False) -> None:
        super().__init__()
        self.alive = True
        self.socket_ready = True
        self.verb_calls: list[tuple[str, bytes, str]] = []
        self.raise_not_implemented = raise_not_implemented

    async def ci_rpc(
        self,
        sandbox_id: str,
        payload: bytes,
        *,
        socket_path: str,
        timeout: int | None = None,
    ) -> bytes:
        del timeout
        self.verb_calls.append((sandbox_id, payload, socket_path))
        if self.raise_not_implemented:
            raise NotImplementedError("verb not yet wired")
        request = _decode_frame(payload)
        response = {
            "v": CI_PROTOCOL_VERSION,
            "id": request["id"],
            "ok": True,
            "result": {"via": "verb", "op": request["op"]},
        }
        return encode_frame(response)


def _decode_frame(payload: bytes) -> dict[str, Any]:
    (length,) = struct.unpack(">I", payload[:4])
    return msgpack.unpackb(payload[4 : 4 + length], raw=False)


@pytest.mark.asyncio
async def test_call_prefers_native_verb_when_available() -> None:
    transport = _VerbTransport()
    client = CiRpcClient(transport, "sb-verb", "/ws")  # type: ignore[arg-type]

    result = await client.call("ping")

    assert result == {"via": "verb", "op": "ping"}
    assert len(transport.verb_calls) == 1
    sandbox_id, _payload, socket_path = transport.verb_calls[0]
    assert sandbox_id == "sb-verb"
    # socket_path was resolved through DaemonLauncher (defaults to /home/u
    # via the FakeTransport's HOME stub).
    assert socket_path.endswith("/daemon.sock")
    # Shim path was NOT exercised — no socket-shim exec was issued.
    assert not any(
        "socket.socket(socket.AF_UNIX)" in cmd for cmd in transport.exec_calls
    )


@pytest.mark.asyncio
async def test_call_falls_back_to_shim_when_force_shim_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EOS_CI_FORCE_SHIM", "1")
    transport = _VerbTransport()
    client = CiRpcClient(transport, "sb-shim", "/ws")  # type: ignore[arg-type]

    result = await client.call("ping")

    # Shim returned the result instead of the verb.
    assert result == {"pong": True, "op": "ping"}
    assert transport.verb_calls == []
    assert any(
        "socket.socket(socket.AF_UNIX)" in cmd for cmd in transport.exec_calls
    )


@pytest.mark.asyncio
async def test_verb_not_implemented_falls_back_to_shim() -> None:
    transport = _VerbTransport(raise_not_implemented=True)
    client = CiRpcClient(transport, "sb-fallback", "/ws")  # type: ignore[arg-type]

    result = await client.call("ping")

    # Verb was attempted, then the shim took over transparently.
    assert len(transport.verb_calls) == 1
    assert result == {"pong": True, "op": "ping"}


@pytest.mark.asyncio
async def test_force_shim_re_read_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EOS_CI_FORCE_SHIM is re-checked on every call (per-call os.environ.get)."""
    transport = _VerbTransport()
    client = CiRpcClient(transport, "sb-ab", "/ws")  # type: ignore[arg-type]

    # First call: flag unset -> verb path.
    await client.call("ping")
    assert len(transport.verb_calls) == 1

    # Flip flag mid-process: subsequent call must take the shim path
    # without rebuilding the client.
    monkeypatch.setenv("EOS_CI_FORCE_SHIM", "1")
    await client.call("ping")
    assert len(transport.verb_calls) == 1  # no new verb call
    assert any(
        "socket.socket(socket.AF_UNIX)" in cmd for cmd in transport.exec_calls
    )
