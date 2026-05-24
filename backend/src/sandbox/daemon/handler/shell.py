"""``api.v1.shell`` dispatch entry."""

from __future__ import annotations

from typing import Any

from sandbox._shared.models import Intent
from sandbox.daemon.dispatch import run_tool_handler


async def shell(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="shell", intent=Intent.WRITE_ALLOWED)


__all__ = ["shell"]
