"""LSP-style JSON-RPC framing over an :class:`asyncio.StreamReader`.

The LSP wire protocol prefixes every JSON body with a ``Content-Length``
header followed by ``\\r\\n\\r\\n`` and the UTF-8 body. This module owns the
framing primitives that Phase 3.6's :class:`LspBackendChild` uses to talk
to the basedpyright langserver subprocess.

Public API (every entry has a matching unit test in
``test_lsp_child.py``):

* :func:`encode_request` — frame a JSON-RPC ``request`` payload (id + method).
* :func:`encode_notification` — frame a JSON-RPC ``notification`` payload (no id).
* :func:`encode_response` — frame a JSON-RPC ``response`` payload (used in tests).
* :func:`read_frame` — consume exactly one Content-Length-framed JSON message.
* :class:`FrameError` — raised on a malformed header (missing ``Content-Length``).

Design choices fixed by the qualification spike (Stage A):

1. The reader MUST loop past server-initiated notifications until it sees the
   matching response id. Naive read-once-and-return produces hangs because
   basedpyright sends ``window/logMessage`` and ``$/progress`` before
   responding to ``initialize``.
2. Headers are case-insensitive per RFC 5322 — ``content-length`` from a
   wonky child must still parse.
3. Body decode uses ``readexactly`` semantics — partial reads under back
   pressure return None instead of silently truncating.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

__all__ = [
    "FrameError",
    "encode_notification",
    "encode_request",
    "encode_response",
    "read_frame",
]


class FrameError(Exception):
    """Raised when an inbound frame violates the Content-Length contract."""


def encode_request(req_id: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    """Frame a JSON-RPC request: ``id`` is correlated to the response."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params if params is not None else {},
        }
    ).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def encode_notification(method: str, params: dict[str, Any] | None = None) -> bytes:
    """Frame a JSON-RPC notification (no ``id``, no expected response)."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
        }
    ).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def encode_response(req_id: int, result: Any) -> bytes:
    """Frame a JSON-RPC response — exposed so tests can build fake servers."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "result": result}
    ).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read exactly one Content-Length-framed JSON-RPC message.

    Returns the decoded dict.

    Raises:
        :class:`asyncio.IncompleteReadError` — connection closed mid-frame.
        :class:`FrameError` — header missing ``Content-Length`` or body truncated.
        :class:`json.JSONDecodeError` — body is not valid JSON.
    """
    length: int | None = None
    while True:
        line = await reader.readline()
        if not line:
            raise asyncio.IncompleteReadError(b"", None)
        line = line.rstrip(b"\r\n")
        if not line:
            break  # End of headers.
        # Case-insensitive header match — some LSP servers lowercase the
        # canonical header.
        lower = line.lower()
        if lower.startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError as exc:
                raise FrameError(
                    f"unparseable Content-Length value: {line!r}"
                ) from exc
    if length is None:
        raise FrameError("missing Content-Length header")
    body = await reader.readexactly(length)
    return json.loads(body.decode("utf-8"))
