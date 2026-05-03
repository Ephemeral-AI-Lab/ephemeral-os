"""Runtime handler for OCC-gated shell requests."""

from __future__ import annotations

from typing import Any

from sandbox.overlay.wire import shell_result_to_dict
from sandbox.runtime.pipelines import shell_pipeline


async def handle(args: dict[str, Any]) -> dict[str, Any]:
    timeout_raw = args.get("timeout")
    result = await shell_pipeline(
        command=str(args["command"]),
        workspace_root=str(args.get("workspace_root") or "/workspace"),
        sandbox_id=str(args.get("sandbox_id") or "local"),
        timeout=int(timeout_raw) if timeout_raw is not None else None,
        stdin=args.get("stdin"),
        description=str(args.get("description") or ""),
        agent_id=str(args.get("agent_id") or ""),
    )
    return shell_result_to_dict(result)


__all__ = ["handle"]
