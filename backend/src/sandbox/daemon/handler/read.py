"""``api.v1.read_file`` dispatch entry."""

from __future__ import annotations

from typing import Any

from sandbox._shared.models import Intent
from sandbox.daemon.dispatch import run_tool_handler


async def read_file(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="read_file", intent=Intent.READ_ONLY)


__all__ = ["read_file"]
