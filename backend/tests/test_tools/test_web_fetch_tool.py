"""Tests for web fetch and search tools."""

from __future__ import annotations

import contextlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from urllib.parse import parse_qs, urlparse

from ephemeralos.tools.base import ToolExecutionContext
from ephemeralos.tools.web_fetch_tool import WebFetchTool, WebFetchToolInput
from ephemeralos.tools.web_search_tool import WebSearchTool, WebSearchToolInput


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
        if query:
            body = (
                "<html><body>"
                '<a class="result__a" href="https://example.com/docs">EphemeralOS Docs</a>'
                '<div class="result__snippet">Search query was %s and docs were found.</div>'
                "</body></html>"
            ) % query
        else:
            body = "<html><body><h1>EphemeralOS Test</h1><p>web fetch works</p></body></html>"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        del format, args


@pytest.mark.asyncio
async def test_web_fetch_tool_reads_html(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        tool = WebFetchTool()
        result = await tool.execute(
            WebFetchToolInput(url=f"http://127.0.0.1:{server.server_port}/"),
            ToolExecutionContext(cwd=tmp_path),
        )
    finally:
        server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()
        thread.join(timeout=1)

    assert result.is_error is False
    assert "EphemeralOS Test" in result.output
    assert "web fetch works" in result.output


@pytest.mark.asyncio
async def test_web_search_tool_reads_results(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        tool = WebSearchTool()
        result = await tool.execute(
            WebSearchToolInput(
                query="ephemeralos docs",
                search_url=f"http://127.0.0.1:{server.server_port}/search",
            ),
            ToolExecutionContext(cwd=tmp_path),
        )
    finally:
        server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()
        thread.join(timeout=1)

    assert result.is_error is False
    assert "EphemeralOS Docs" in result.output
    assert "https://example.com/docs" in result.output
    assert "ephemeralos docs" in result.output
