"""Unit tests for the Phase 3.6 :class:`LspBackendChild` + JSON-RPC framing.

These tests use ``asyncio.StreamReader`` / a mock subprocess so we exercise
the full request/response correlation, restart-on-crash, and shutdown
contracts without touching a real basedpyright binary.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sandbox.code_intelligence.language_server.jsonrpc import (
    FrameError,
    encode_notification,
    encode_request,
    encode_response,
    read_frame,
)
from sandbox.code_intelligence.language_server.lsp_child import (
    LSP_BACKEND_CHOSEN,
    LspBackendChild,
    LspChildCrashed,
    LspChildUnavailable,
    _file_uri,
    _normalize_locations,
    _parse_diagnostic,
    _parse_hover,
    _parse_location_to_reference,
    _parse_location_to_symbol,
    _uri_to_path,
)


# ---------------------------------------------------------------------------
# Frame round-trip
# ---------------------------------------------------------------------------


def _stream_from_bytes(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_encode_request_round_trip_via_read_frame() -> None:
    payload = encode_request(7, "initialize", {"processId": 42})
    msg = await read_frame(_stream_from_bytes(payload))
    assert msg["id"] == 7
    assert msg["method"] == "initialize"
    assert msg["params"]["processId"] == 42
    assert msg["jsonrpc"] == "2.0"


@pytest.mark.asyncio
async def test_encode_notification_has_no_id() -> None:
    payload = encode_notification("initialized", {})
    msg = await read_frame(_stream_from_bytes(payload))
    assert "id" not in msg
    assert msg["method"] == "initialized"


@pytest.mark.asyncio
async def test_read_frame_case_insensitive_header() -> None:
    body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    raw = b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    msg = await read_frame(_stream_from_bytes(raw))
    assert msg["id"] == 1


@pytest.mark.asyncio
async def test_read_frame_raises_on_missing_header() -> None:
    raw = b"\r\n" + b'{"jsonrpc":"2.0","id":1,"result":{}}'
    with pytest.raises(FrameError):
        await read_frame(_stream_from_bytes(raw))


@pytest.mark.asyncio
async def test_read_frame_raises_on_eof() -> None:
    reader = asyncio.StreamReader()
    reader.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)


# ---------------------------------------------------------------------------
# LSP → CI types parsing
# ---------------------------------------------------------------------------


def test_file_uri_round_trip() -> None:
    assert _file_uri("/abs/path.py") == "file:///abs/path.py"
    assert _uri_to_path("file:///abs/path.py") == "/abs/path.py"
    # idempotent
    assert _file_uri("file:///already") == "file:///already"


def test_normalize_locations_handles_all_lsp_shapes() -> None:
    assert _normalize_locations(None) == []
    loc = {"uri": "file:///a.py", "range": {"start": {"line": 0, "character": 0}}}
    assert _normalize_locations(loc) == [loc]
    assert _normalize_locations([loc, loc]) == [loc, loc]


def test_parse_location_to_symbol_uses_targetRange_for_LocationLink() -> None:
    link = {
        "targetUri": "file:///a.py",
        "targetRange": {"start": {"line": 5, "character": 10}, "end": {"line": 5, "character": 20}},
    }
    sym = _parse_location_to_symbol(link)
    assert sym.file_path == "/a.py"
    assert sym.line == 5
    assert sym.character == 10


def test_parse_location_to_reference_extracts_position() -> None:
    loc = {"uri": "file:///b.py", "range": {"start": {"line": 7, "character": 4}}}
    ref = _parse_location_to_reference(loc)
    assert ref.file_path == "/b.py"
    assert ref.line == 7
    assert ref.character == 4


def test_parse_hover_string_form() -> None:
    h = _parse_hover({"contents": "def foo()"})
    assert h is not None
    assert h.content == "def foo()"


def test_parse_hover_markup_form() -> None:
    h = _parse_hover({"contents": {"value": "**bold**", "language": "markdown"}})
    assert h is not None
    assert h.content == "**bold**"
    assert h.language == "markdown"


def test_parse_hover_list_form() -> None:
    h = _parse_hover({"contents": ["line one", {"value": "line two"}]})
    assert h is not None
    assert "line one" in h.content
    assert "line two" in h.content


def test_parse_hover_none_returns_none() -> None:
    assert _parse_hover(None) is None
    assert _parse_hover({}) is None


def test_parse_diagnostic_default_severity() -> None:
    item = {
        "range": {"start": {"line": 3, "character": 0}, "end": {"line": 3, "character": 10}},
        "message": "boom",
    }
    diag = _parse_diagnostic(item, "/c.py")
    assert diag.line == 3
    assert diag.message == "boom"
    assert diag.file_path == "/c.py"


def test_parse_diagnostic_severity_mapping() -> None:
    from sandbox.code_intelligence.core.types import DiagnosticSeverity

    item = {
        "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 1}},
        "severity": 2,
        "message": "warn",
    }
    diag = _parse_diagnostic(item, "/c.py")
    assert diag.severity is DiagnosticSeverity.WARNING


# ---------------------------------------------------------------------------
# LspBackendChild — restart-on-crash + missing-binary
# ---------------------------------------------------------------------------


def test_lsp_backend_chosen_is_basedpyright() -> None:
    """Stage A locked the chosen backend in lsp-qualification-spike-result.md."""
    assert LSP_BACKEND_CHOSEN == "basedpyright"


@pytest.mark.asyncio
async def test_start_raises_unavailable_when_binary_missing() -> None:
    child = LspBackendChild(workspace_root="/ws")

    async def _raise(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "No such file or directory: 'basedpyright-langserver'")

    with patch("asyncio.create_subprocess_exec", side_effect=_raise):
        with pytest.raises(LspChildUnavailable, match="basedpyright"):
            await child.start()


# ---------------------------------------------------------------------------
# Round-trip: fake subprocess driven by a stream pair
# ---------------------------------------------------------------------------


class _FakeProc:
    """Minimal subprocess.Process surface backed by asyncio streams in-memory."""

    def __init__(
        self,
        *,
        canned_responses: dict[str, Any] | None = None,
        crash_after_initialize: bool = False,
    ) -> None:
        self._canned = canned_responses or {}
        self._crash_after_initialize = crash_after_initialize
        self._stdin_buf = bytearray()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = _RecordingStdin(self)
        self._dead = False

    async def wait(self) -> int:
        while not self._dead:
            await asyncio.sleep(0.01)
        return 0

    def terminate(self) -> None:
        self._dead = True
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def kill(self) -> None:
        self.terminate()

    async def _react(self) -> None:
        """Drain stdin, parse one frame, push the canned response."""
        while True:
            await asyncio.sleep(0.01)
            if not self._stdin_buf:
                continue
            try:
                frame, consumed = _try_parse_frame(bytes(self._stdin_buf))
            except FrameError:
                return
            if frame is None:
                continue
            del self._stdin_buf[:consumed]
            method = frame.get("method") or ""
            if method == "initialize":
                req_id = frame.get("id")
                self.stdout.feed_data(encode_response(req_id, {"capabilities": {}}))
                if self._crash_after_initialize:
                    self.terminate()
                    return
            elif method == "shutdown":
                req_id = frame.get("id")
                self.stdout.feed_data(encode_response(req_id, None))
            elif method == "exit":
                self.terminate()
                return
            elif "id" in frame:
                req_id = frame["id"]
                resp = self._canned.get(method, [])
                self.stdout.feed_data(encode_response(req_id, resp))
            # notifications (initialized, didOpen, didChange) — ignore.


def _try_parse_frame(buf: bytes) -> tuple[dict[str, Any] | None, int]:
    header_end = buf.find(b"\r\n\r\n")
    if header_end == -1:
        return None, 0
    headers = buf[:header_end].decode("ascii", "replace")
    length = None
    for line in headers.split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if length is None:
        raise FrameError("no Content-Length")
    total = header_end + 4 + length
    if len(buf) < total:
        return None, 0
    body = buf[header_end + 4 : total]
    return json.loads(body.decode("utf-8")), total


class _RecordingStdin:
    def __init__(self, owner: _FakeProc) -> None:
        self._owner = owner

    def write(self, data: bytes) -> None:
        self._owner._stdin_buf.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_handshake_and_find_definitions_round_trip() -> None:
    canned = {
        "textDocument/definition": [
            {
                "uri": "file:///ws/foo.py",
                "range": {"start": {"line": 5, "character": 4}, "end": {"line": 5, "character": 8}},
            }
        ]
    }
    fake = _FakeProc(canned_responses=canned)
    react_task = asyncio.create_task(fake._react())
    try:
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake),
        ):
            child = LspBackendChild(workspace_root="/ws")
            await child.start(init_timeout_s=2.0)
            # didOpen reads the file from disk; supply a tmp file.
            import tempfile
            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", delete=False
            ) as fh:
                fh.write("def foo():\n    pass\n")
                tmp_path = fh.name
            results = await child.find_definitions(tmp_path, line=0, character=4)
            assert len(results) == 1
            assert results[0].file_path == "/ws/foo.py"
            assert results[0].line == 5
    finally:
        react_task.cancel()
        try:
            await react_task
        except asyncio.CancelledError:
            pass
        await child.shutdown(timeout_s=0.5)


@pytest.mark.asyncio
async def test_outstanding_request_fails_with_crashed_on_eof() -> None:
    fake = _FakeProc()
    react_task = asyncio.create_task(fake._react())
    try:
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake),
        ):
            child = LspBackendChild(workspace_root="/ws")
            await child.start(init_timeout_s=2.0)
            # Stop the fake from responding to anything else, then send a
            # request and crash the child mid-flight.
            react_task.cancel()
            try:
                await react_task
            except asyncio.CancelledError:
                pass
            send = asyncio.create_task(child._send_request("noResponse", {}))
            await asyncio.sleep(0.02)
            fake.terminate()  # EOF mid-request
            with pytest.raises(LspChildCrashed):
                await asyncio.wait_for(send, timeout=2.0)
    finally:
        if not react_task.done():
            react_task.cancel()
            try:
                await react_task
            except asyncio.CancelledError:
                pass

