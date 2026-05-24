"""Host-side enter-isolated-workspace coroutine."""

from __future__ import annotations

import os

from sandbox._shared.models import (
    EnterIsolatedWorkspaceRequest,
    EnterIsolatedWorkspaceResult,
    LifecycleError,
)
from sandbox.audit.lifecycle import lifecycle_operation
from sandbox.isolated_workspace import handlers
from sandbox.isolated_workspace.pipeline import IsolatedWorkspaceError


async def enter_isolated_workspace(
    req: EnterIsolatedWorkspaceRequest,
) -> EnterIsolatedWorkspaceResult:
    agent_id = req.caller.agent_id
    try:
        async with lifecycle_operation(
            kind="enter_isolated_workspace",
            actor_id=agent_id,
            audit_path=os.environ.get("EOS_WORKSPACE_LIFECYCLE_AUDIT_PATH"),
        ) as timings:
            manager = await handlers._ensure_manager(  # noqa: SLF001
                {"layer_stack_root": req.layer_stack_root}
            )
            handle = await manager.enter(agent_id)
            return EnterIsolatedWorkspaceResult(
                success=True,
                manifest_version=str(handle.manifest_version),
                manifest_root_hash=str(handle.manifest_root_hash),
                timings=dict(timings),
            )
    except IsolatedWorkspaceError as exc:
        return EnterIsolatedWorkspaceResult(
            success=False,
            error=LifecycleError(
                kind=exc.kind,
                message=str(exc),
                details={str(k): str(v) for k, v in exc.details.items()},
            ),
        )


__all__ = ["enter_isolated_workspace"]
