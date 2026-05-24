"""Host-side exit-isolated-workspace coroutine."""

from __future__ import annotations

import os

from sandbox._shared.models import (
    ExitIsolatedWorkspaceRequest,
    ExitIsolatedWorkspaceResult,
    LifecycleError,
)
from sandbox.audit.lifecycle import lifecycle_operation
from sandbox.isolated_workspace.pipeline import IsolatedWorkspaceError, require_pipeline


async def exit_isolated_workspace(
    req: ExitIsolatedWorkspaceRequest,
) -> ExitIsolatedWorkspaceResult:
    agent_id = req.caller.agent_id
    try:
        async with lifecycle_operation(
            kind="exit_isolated_workspace",
            actor_id=agent_id,
            audit_path=os.environ.get("EOS_WORKSPACE_LIFECYCLE_AUDIT_PATH"),
        ) as timings:
            result = await require_pipeline().exit(agent_id, grace_s=req.grace_s)
            timings.update(result.get("phases_ms", {}))
            return ExitIsolatedWorkspaceResult(
                success=bool(result.get("success", True)),
                evicted_upperdir_bytes=int(result.get("evicted_upperdir_bytes", 0)),
                lifetime_s=float(result.get("lifetime_s", 0.0)),
                phases_ms=dict(result.get("phases_ms", {})),
                timings=dict(timings),
            )
    except IsolatedWorkspaceError as exc:
        return ExitIsolatedWorkspaceResult(
            success=False,
            error=LifecycleError(
                kind=exc.kind,
                message=str(exc),
                details={str(k): str(v) for k, v in exc.details.items()},
            ),
        )


__all__ = ["exit_isolated_workspace"]
