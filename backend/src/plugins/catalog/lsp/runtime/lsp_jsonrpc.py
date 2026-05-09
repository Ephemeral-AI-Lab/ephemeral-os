"""Minimal LSP JSON-RPC 2.0 framing + request/response correlation."""

from __future__ import annotations

import asyncio
import json
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = [
    "JsonRpcError",
    "LspJsonRpcClient",
    "LspProtocolError",
    "encode_message",
    "parse_header",
]


logger = logging.getLogger(__name__)


class LspProtocolError(RuntimeError):
    """Raised on framing or protocol-level decode failures."""


class JsonRpcError(RuntimeError):
    """Raised when an LSP server returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(f"LSP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


def encode_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def parse_header(buffer: bytes) -> tuple[int, int] | None:
    """Return ``(content_length, header_bytes)`` if a header is complete."""
    sep = buffer.find(b"\r\n\r\n")
    if sep == -1:
        return None
    header = buffer[:sep].decode("ascii", errors="replace")
    length: int | None = None
    for line in header.split("\r\n"):
        if not line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError as exc:
                raise LspProtocolError(
                    f"invalid Content-Length: {value!r}"
                ) from exc
    if length is None:
        raise LspProtocolError("missing Content-Length header")
    return length, sep + 4


class LspJsonRpcClient:
    """Async JSON-RPC client over a subprocess's stdin/stdout."""

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        *,
        request_timeout_s: float = 30.0,
        server_request_handler: Callable[[dict[str, Any]], Awaitable[Any] | Any]
        | None = None,
    ) -> None:
        self._writer = writer
        self._reader = reader
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._request_timeout_s = request_timeout_s
        self._notifications: list[Callable[[dict[str, Any]], Awaitable[None]]] = []
        self._server_request_handler = server_request_handler
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_loop())

    def add_notification_handler(
        self, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self._notifications.append(handler)

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._writer.write(encode_message(message))
            await self._writer.drain()
            return await asyncio.wait_for(future, timeout=self._request_timeout_s)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        self._writer.write(encode_message(message))
        await self._writer.drain()

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            logger.debug("LSP writer close error", exc_info=True)

    async def _read_loop(self) -> None:
        buffer = b""
        try:
            while not self._closed:
                while True:
                    parsed = parse_header(buffer)
                    if parsed is not None:
                        break
                    chunk = await self._reader.read(4096)
                    if not chunk:
                        return
                    buffer += chunk
                content_length, header_end = parsed
                while len(buffer) < header_end + content_length:
                    chunk = await self._reader.read(4096)
                    if not chunk:
                        return
                    buffer += chunk
                body = buffer[header_end : header_end + content_length]
                buffer = buffer[header_end + content_length :]
                try:
                    message = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.debug("LSP unparseable body", exc_info=True)
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("LSP read loop error", exc_info=True)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = int(message["id"])
            future = self._pending.get(request_id)
            if future is None or future.done():
                return
            if "error" in message:
                err = message["error"] or {}
                future.set_exception(
                    JsonRpcError(
                        code=int(err.get("code", -1)),
                        message=str(err.get("message", "")),
                        data=err.get("data"),
                    )
                )
            else:
                future.set_result(message.get("result"))
            return
        if "id" in message and isinstance(message.get("method"), str):
            await self._respond_to_server_request(message)
            return
        method = message.get("method")
        if not method:
            return
        for handler in self._notifications:
            try:
                await handler(message)
            except Exception:
                logger.debug(
                    "LSP notification handler error",
                    exc_info=True,
                )

    async def _respond_to_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        try:
            result: Any = None
            if self._server_request_handler is not None:
                maybe_result = self._server_request_handler(message)
                if inspect.isawaitable(maybe_result):
                    result = await maybe_result
                else:
                    result = maybe_result
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            logger.debug("LSP server request handler error", exc_info=True)
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": str(exc)},
            }
        self._writer.write(encode_message(response))
        await self._writer.drain()
