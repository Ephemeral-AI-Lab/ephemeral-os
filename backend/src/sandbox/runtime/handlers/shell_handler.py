"""``api.shell`` dispatch entry.

Thin wrapper that delegates to ``command_exec_server`` workers — that
module owns the mount/capture/OCC-apply pipeline and the shell-specific
service cache.
"""

from __future__ import annotations


async def shell(args: dict[str, object]) -> dict[str, object]:
    # Late import to avoid pulling the worker scaffolding at module load,
    # mirroring the pattern used by ``api_handlers.drop_services_cache``.
    from sandbox.runtime import command_exec_server

    layer_stack, occ_client, gitignore, storage_root = command_exec_server._services(
        args
    )
    result = await command_exec_server._execute_shell(
        args,
        layer_stack=layer_stack,
        occ_client=occ_client,
        gitignore=gitignore,
        storage_root=storage_root,
    )
    return command_exec_server._payload_from_result(result)


__all__ = ["shell"]
