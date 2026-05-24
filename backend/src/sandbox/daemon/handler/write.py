"""``api.v1.write_file`` dispatch entry."""

from __future__ import annotations

from typing import Any

from sandbox._shared.models import Intent
from sandbox.daemon.dispatch import run_tool_handler


async def write_file(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="write_file", intent=Intent.WRITE_ALLOWED)


__all__ = ["write_file"]
