"""Orchestrator-side RPC client for the in-sandbox CI daemon."""

from __future__ import annotations

import asyncio
import base64
import textwrap
import uuid
from typing import Any

from sandbox.api.transport import SandboxTransport
from sandbox.code_intelligence.in_sandbox.ci_protocol import (
    CI_PROTOCOL_VERSION,
    encode_frame,
    parse_response,
    read_frame,
)
from sandbox.code_intelligence.rpc.launcher import (
    CiDaemonUnavailable,
    DaemonLauncher,
)

__all__ = ["CiDaemonRpcError", "CiDaemonUnavailable", "CiRpcClient"]


class CiDaemonRpcError(Exception):
    """Raised when the daemon returns an ``ok=False`` error envelope."""

    def __init__(
        self,
        kind: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class CiRpcClient:
    """Small RPC client using ``transport.exec`` as the Phase 2 socket shim."""

    def __init__(
        self,
        transport: SandboxTransport,
        sandbox_id: str,
        workspace_root: str,
    ) -> None:
        self._transport = transport
        self._sandbox_id = sandbox_id
        self._launcher = DaemonLauncher(transport, sandbox_id, workspace_root)

    async def call(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        """Send one request and return the result.

        Connection failures trigger one ``ensure_daemon`` retry. Daemon-side
        error envelopes are returned as :class:`CiDaemonRpcError` without retry.
        """
        try:
            return await self._call_once(op, args or {}, timeout=timeout)
        except (ConnectionRefusedError, BrokenPipeError, FileNotFoundError, OSError):
            await self._launcher.ensure_daemon()
            try:
                return await self._call_once(op, args or {}, timeout=timeout)
            except (
                ConnectionRefusedError,
                BrokenPipeError,
                FileNotFoundError,
                OSError,
            ) as exc:
                raise CiDaemonUnavailable(
                    f"daemon unreachable after respawn: {exc}"
                ) from exc

    async def _call_once(
        self,
        op: str,
        args: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        request_id = uuid.uuid4().hex
        frame = encode_frame(
            {"v": CI_PROTOCOL_VERSION, "id": request_id, "op": op, "args": args}
        )
        socket_path = await self._launcher.socket_path()
        response_frame = await self._send_frame_via_python_shim(
            socket_path,
            frame,
            timeout=timeout,
        )
        reader = asyncio.StreamReader()
        reader.feed_data(response_frame)
        reader.feed_eof()
        response = parse_response(await read_frame(reader))
        if response.id != request_id:
            raise RuntimeError(
                f"daemon response id mismatch: expected {request_id}, got {response.id}"
            )
        if not response.ok:
            error = response.error or {}
            raise CiDaemonRpcError(
                kind=str(error.get("kind") or "InternalError"),
                message=str(error.get("message") or ""),
                details=error.get("details") if isinstance(error.get("details"), dict) else {},
            )
        return response.result

    async def _send_frame_via_python_shim(
        self,
        socket_path: str,
        frame: bytes,
        *,
        timeout: float,
    ) -> bytes:
        """Send ``frame`` through a sandbox-local Python Unix-socket shim."""
        encoded = base64.b64encode(frame).decode("ascii")
        script = textwrap.dedent(
            f"""
            import base64
            import socket
            import sys

            frame = base64.b64decode({encoded!r})
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout({float(timeout)!r})
            sock.connect({socket_path!r})
            sock.sendall(frame)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
            sock.close()
            sys.stdout.write(base64.b64encode(b"".join(chunks)).decode("ascii"))
            """
        ).strip()
        command = f"python3 - <<'PY'\n{script}\nPY"
        result = await self._transport.exec(
            self._sandbox_id,
            command,
            timeout=max(1, int(timeout) + 5),
        )
        stdout = (getattr(result, "stdout", "") or "").strip()
        if getattr(result, "exit_code", 1) != 0:
            raise ConnectionRefusedError(stdout)
        try:
            return base64.b64decode(stdout)
        except Exception as exc:
            raise ConnectionRefusedError(
                f"daemon shim produced invalid base64: {stdout!r}"
            ) from exc
