"""``api.v1.glob`` dispatch entry."""

from __future__ import annotations

from typing import Any

from sandbox._shared.models import Intent
from sandbox.daemon.dispatch import run_tool_handler


async def glob(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="glob", intent=Intent.READ_ONLY)


__all__ = ["glob"]
