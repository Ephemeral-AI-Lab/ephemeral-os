"""Compatibility helpers for the third-party `mcp` SDK.

This project intentionally uses a local `ephemeralos.mcp` package, which shares
the top-level `mcp` name with the upstream MCP SDK package. Imports like
`from mcp import ClientSession` would otherwise resolve to the local package.
Use these helpers wherever MCP SDK symbols are required.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import PathFinder
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

_LOCAL_MCP_INIT: Final = Path(__file__).resolve().parent / "__init__.py"
_MCP_CACHE: ModuleType | None = None


def load_external_mcp() -> ModuleType:
    """Return the installed third-party `mcp` package even when local `mcp` exists."""
    global _MCP_CACHE

    if _MCP_CACHE is not None:
        return _MCP_CACHE

    local_root = Path(_LOCAL_MCP_INIT).resolve().parent.parent
    search_paths = [
        path
        for path in sys.path
        if path and Path(path).resolve() != local_root
    ]
    spec = PathFinder.find_spec("mcp", search_paths)
    if spec is None or spec.origin is None:
        raise ModuleNotFoundError("Could not locate the third-party `mcp` package.")

    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous_mcp = sys.modules.get("mcp")
    try:
        sys.modules["mcp"] = module
        spec.loader.exec_module(module)
    finally:
        if previous_mcp is None:
            sys.modules.pop("mcp", None)
        else:
            sys.modules["mcp"] = previous_mcp

    _MCP_CACHE = module
    return module
