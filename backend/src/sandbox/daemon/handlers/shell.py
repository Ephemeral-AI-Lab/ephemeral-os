"""``api.shell`` dispatch entry."""

from __future__ import annotations


async def shell(args: dict[str, object]) -> dict[str, object]:
    # Late import to avoid pulling the worker scaffolding at module load,
    # keeping the public handler package cheap to import.
    from sandbox.daemon.services import shell_runner

    return await shell_runner.execute_shell_api(args)


__all__ = ["shell"]
