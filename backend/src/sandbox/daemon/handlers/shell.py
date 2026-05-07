"""``api.shell`` dispatch entry.

Thin wrapper that delegates to ``command_exec_server`` workers — that
module owns the mount/capture/OCC-apply pipeline and the shell-specific
service wiring.
"""

from __future__ import annotations


async def shell(args: dict[str, object]) -> dict[str, object]:
    # Late import to avoid pulling the worker scaffolding at module load,
    # keeping the public handler package cheap to import.
    from sandbox.daemon import command_exec_server

    return await command_exec_server.execute_shell_api(args)


__all__ = ["shell"]
