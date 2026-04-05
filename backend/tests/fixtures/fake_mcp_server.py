"""Small stdio MCP server used by integration tests."""

from __future__ import annotations

from pathlib import Path
import sys

try:
    from mcp._compat import load_external_mcp
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from mcp._compat import load_external_mcp

FastMCP = load_external_mcp().server.fastmcp.FastMCP

server = FastMCP("fixture-demo")


@server.tool()
def hello(name: str) -> str:
    return f"fixture-hello:{name}"


@server.resource("fixture://readme", name="Fixture Readme")
def readme() -> str:
    return "fixture resource contents"


if __name__ == "__main__":
    server.run("stdio")
